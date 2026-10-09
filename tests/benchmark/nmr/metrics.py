"""Layered benchmark metrics (todo 50 / gap §10.3).

Per dataset the harness reports:

* **shift accuracy** — per-nucleus MAE/RMSE over the TRUE structure
  candidate's paired residuals (signal matched by ``signal_id``), with a
  molecule-clustered bootstrap CI;
* **assignment accuracy** — fraction of paired signals whose predicted
  ``atom_label`` equals the experimental label (true candidate only);
* **Top-1** — whether the true candidate ranks first by DP4 (primary) and by
  DP5 (among candidates with an available DP5 probability);
* **true-structure absence** — typed per-item behavior when no true structure
  exists: ``refused`` (no valid candidate) vs ``true_structure_absent`` with a
  confidence flag (top DP4 >= 0.5), plus the aggregate confident-winner rate;
* **binary probability** — DP5 (and DP4) positive/negative Brier, log-loss
  and calibration-curve points; ``null`` probabilities are excluded, never
  treated as 0;
* **failure/refusal** — non-valid candidates by status and refused items;
* **resources** — wall/CPU/peak-RSS accounting is attached by the harness.

Every metric block carries a ``status`` of ``measured`` or ``not_verified``:
missing data is never a silent pass and never a fabricated number.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from tests.benchmark.nmr.bootstrap import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_RESAMPLES,
    clustered_bootstrap_ci,
    pooled_mean,
)
from tests.benchmark.nmr.schema import NUCLEI

#: A true-absent item counts as a "confident winner" at/above this DP4 value.
ABSENT_CONFIDENCE_THRESHOLD = 0.5

#: Number of equal-width bins for the calibration curve.
CALIBRATION_BINS = 10

#: Probability clamp for log-loss (never log(0)).
LOG_LOSS_EPSILON = 1e-15

_NEGATIVE_INFINITY = float("-inf")


def _cluster(values: Sequence[tuple[str, float]]) -> dict[str, list[float]]:
    clusters: dict[str, list[float]] = {}
    for molecule_id, value in values:
        clusters.setdefault(molecule_id, []).append(value)
    return clusters


def _ci_or_reason(
    clusters: Mapping[str, Sequence[float]],
    *,
    seed: int,
    n_resamples: int,
    statistic: Any = pooled_mean,
) -> tuple[dict[str, Any] | None, str | None]:
    if len(clusters) < 2:
        return None, "insufficient_molecule_clusters"
    interval = clustered_bootstrap_ci(
        clusters, statistic=statistic, seed=seed, n_resamples=n_resamples
    )
    return interval.as_dict(), None


def _rank_key(candidate: Mapping[str, Any]) -> tuple[float, float, str]:
    dp5 = candidate["dp5_probability"]
    return (
        -float(candidate["dp4_probability"]),
        -(float(dp5) if dp5 is not None else _NEGATIVE_INFINITY),
        str(candidate["candidate_id"]),
    )


def _rank_dp5_key(candidate: Mapping[str, Any]) -> tuple[float, float, str]:
    return (
        -float(candidate["dp5_probability"]),
        -float(candidate["dp4_probability"]),
        str(candidate["candidate_id"]),
    )


def _evaluate_item(item: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate one item; returns (item record, contribution bundle)."""
    candidates = list(item["candidates"])
    valid = [candidate for candidate in candidates if candidate["status"] == "valid"]
    ranked = sorted(valid, key=_rank_key)
    top1_candidate_id = str(ranked[0]["candidate_id"]) if ranked else None
    top1_is_true = None
    if ranked:
        top1_is_true = bool(ranked[0]["is_true_structure"])
    dp5_ranked = sorted(
        (candidate for candidate in valid if candidate["dp5_probability"] is not None),
        key=_rank_dp5_key,
    )
    top1_dp5_candidate_id = str(dp5_ranked[0]["candidate_id"]) if dp5_ranked else None
    top1_dp5_is_true = None
    if dp5_ranked:
        top1_dp5_is_true = bool(dp5_ranked[0]["is_true_structure"])

    residuals: dict[str, list[dict[str, Any]]] = {}
    pairing: dict[str, dict[str, list[str]]] = {}
    if item["true_structure_present"]:
        true_candidate = next(
            candidate
            for candidate in candidates
            if candidate["candidate_id"] == item["true_structure_candidate_id"]
        )
        if true_candidate["status"] == "valid":
            for nucleus in sorted(item["experimental"]):
                experimental = {
                    str(signal["signal_id"]): signal for signal in item["experimental"][nucleus]
                }
                predicted = {
                    str(signal["signal_id"]): signal
                    for signal in true_candidate["signals"].get(nucleus, ())
                }
                for signal_id in sorted(set(experimental) & set(predicted)):
                    observed = float(experimental[signal_id]["observed_ppm"])
                    predicted_ppm = float(predicted[signal_id]["predicted_ppm"])
                    label_match = None
                    experimental_label = experimental[signal_id].get("atom_label")
                    predicted_label = predicted[signal_id].get("atom_label")
                    if experimental_label is not None and predicted_label is not None:
                        label_match = experimental_label == predicted_label
                    residuals.setdefault(nucleus, []).append(
                        {
                            "signal_id": signal_id,
                            "observed_ppm": observed,
                            "predicted_ppm": predicted_ppm,
                            "residual_ppm": predicted_ppm - observed,
                            "atom_label_match": label_match,
                        }
                    )
                pairing[nucleus] = {
                    "unmatched_experimental_signal_ids": sorted(set(experimental) - set(predicted)),
                    "unmatched_predicted_signal_ids": sorted(set(predicted) - set(experimental)),
                }

    if item["true_structure_present"]:
        absent_behavior = None
    elif ranked:
        top_probability = float(ranked[0]["dp4_probability"])
        absent_behavior = {
            "status": "true_structure_absent",
            "top_candidate_id": top1_candidate_id,
            "top_probability": top_probability,
            "confident": top_probability >= ABSENT_CONFIDENCE_THRESHOLD,
        }
    else:
        absent_behavior = {
            "status": "refused",
            "top_candidate_id": None,
            "top_probability": None,
            "confident": False,
        }

    reasons: list[str] = []
    measured = bool(valid) or bool(residuals)
    if not measured:
        reasons.append("no_valid_candidates")
    record = {
        "item_id": str(item["item_id"]),
        "molecule_id": str(item["molecule_id"]),
        "true_structure_present": bool(item["true_structure_present"]),
        "status": "measured" if measured else "not_verified",
        "reasons": reasons,
        "n_valid_candidates": len(valid),
        "n_nonvalid_candidates": len(candidates) - len(valid),
        "top1_candidate_id": top1_candidate_id,
        "top1_is_true": top1_is_true,
        "top1_dp5_candidate_id": top1_dp5_candidate_id,
        "top1_dp5_is_true": top1_dp5_is_true,
        "residuals": residuals,
        "pairing": pairing,
        "absent_behavior": absent_behavior,
    }
    contribution = {
        "item_id": record["item_id"],
        "molecule_id": record["molecule_id"],
        "true_structure_present": record["true_structure_present"],
        "valid_candidates": valid,
        "residuals": residuals,
        "absent_behavior": absent_behavior,
    }
    return record, contribution


def _shift_accuracy(
    contributions: Sequence[Mapping[str, Any]], *, seed: int, n_resamples: int
) -> dict[str, Any]:
    present_items = [entry for entry in contributions if entry["true_structure_present"]]
    scored_items = [entry for entry in present_items if entry["residuals"]]
    if not present_items:
        empty_reason = "no_true_structure_items"
    elif not scored_items:
        empty_reason = "no_valid_true_structure_candidate"
    else:
        empty_reason = "no_paired_signals"
    blocks: dict[str, Any] = {}
    for nucleus in NUCLEI:
        rows = [
            (entry["molecule_id"], abs(record["residual_ppm"]))
            for entry in scored_items
            for record in entry["residuals"].get(nucleus, ())
        ]
        if not rows:
            blocks[nucleus] = {
                "status": "not_verified",
                "n_residuals": 0,
                "mae": None,
                "rmse": None,
                "max_abs": None,
                "mae_ci": None,
                "rmse_ci": None,
                "reasons": [empty_reason],
            }
            continue
        values = [value for _, value in rows]
        mae = pooled_mean(values)
        rmse = math.sqrt(pooled_mean([value * value for value in values]))
        clusters = _cluster(rows)
        mae_ci, mae_reason = _ci_or_reason(clusters, seed=seed, n_resamples=n_resamples)
        rmse_ci, rmse_reason = _ci_or_reason(
            _cluster([(molecule, value * value) for molecule, value in rows]),
            seed=seed,
            n_resamples=n_resamples,
            statistic=lambda sample: math.sqrt(pooled_mean(sample)),
        )
        reasons = sorted({reason for reason in (mae_reason, rmse_reason) if reason})
        blocks[nucleus] = {
            "status": "measured",
            "n_residuals": len(values),
            "mae": mae,
            "rmse": rmse,
            "max_abs": max(values),
            "mae_ci": mae_ci,
            "rmse_ci": rmse_ci,
            "reasons": reasons,
        }
    return blocks


def _assignment_accuracy(
    contributions: Sequence[Mapping[str, Any]], *, seed: int, n_resamples: int
) -> dict[str, Any]:
    rows: list[tuple[str, float]] = []
    n_unlabeled = 0
    for entry in contributions:
        for nucleus_records in entry["residuals"].values():
            for record in nucleus_records:
                if record["atom_label_match"] is None:
                    n_unlabeled += 1
                else:
                    rows.append((entry["molecule_id"], 1.0 if record["atom_label_match"] else 0.0))
    if not rows:
        return {
            "status": "not_verified",
            "n_scored": 0,
            "n_correct": 0,
            "n_unlabeled": n_unlabeled,
            "accuracy": None,
            "accuracy_ci": None,
            "reasons": ["no_labeled_pairs"],
        }
    values = [value for _, value in rows]
    accuracy = pooled_mean(values)
    accuracy_ci, reason = _ci_or_reason(_cluster(rows), seed=seed, n_resamples=n_resamples)
    return {
        "status": "measured",
        "n_scored": len(values),
        "n_correct": int(math.fsum(values)),
        "n_unlabeled": n_unlabeled,
        "accuracy": accuracy,
        "accuracy_ci": accuracy_ci,
        "reasons": [reason] if reason else [],
    }


def _top1_block(
    rows: Sequence[tuple[str, bool]],
    *,
    excluded_item_ids: Sequence[str],
    seed: int,
    n_resamples: int,
    empty_reason: str,
) -> dict[str, Any]:
    if not rows:
        return {
            "status": "not_verified",
            "n_items": 0,
            "n_correct": 0,
            "n_excluded_items": len(excluded_item_ids),
            "excluded_item_ids": sorted(excluded_item_ids),
            "accuracy": None,
            "ci": None,
            "reasons": [empty_reason],
        }
    values = [(molecule_id, 1.0 if correct else 0.0) for molecule_id, correct in rows]
    accuracy = pooled_mean([value for _, value in values])
    ci, reason = _ci_or_reason(_cluster(values), seed=seed, n_resamples=n_resamples)
    return {
        "status": "measured",
        "n_items": len(values),
        "n_correct": int(math.fsum(value for _, value in values)),
        "n_excluded_items": len(excluded_item_ids),
        "excluded_item_ids": sorted(excluded_item_ids),
        "accuracy": accuracy,
        "ci": ci,
        "reasons": [reason] if reason else [],
    }


def _binary_probability(
    rows: Sequence[Mapping[str, Any]],
    *,
    distribution_note: str,
    seed: int,
    n_resamples: int,
) -> dict[str, Any]:
    if not rows:
        return {
            "status": "not_verified",
            "n_scored": 0,
            "n_positive": 0,
            "n_negative": 0,
            "prevalence": None,
            "brier": None,
            "brier_ci": None,
            "log_loss": None,
            "log_loss_ci": None,
            "clamped": 0,
            "calibration": None,
            "distribution_note": distribution_note,
            "reasons": ["no_probability_data"],
        }
    labels = [float(row["label"]) for row in rows]
    probabilities = [float(row["probability"]) for row in rows]
    brier_rows = [
        (str(row["molecule_id"]), (probability - label) ** 2)
        for row, probability, label in zip(rows, probabilities, labels, strict=True)
    ]
    brier = pooled_mean([value for _, value in brier_rows])
    brier_ci, brier_reason = _ci_or_reason(_cluster(brier_rows), seed=seed, n_resamples=n_resamples)
    clamped = 0
    loss_rows: list[tuple[str, float]] = []
    for row, probability, label in zip(rows, probabilities, labels, strict=True):
        clipped = min(max(probability, LOG_LOSS_EPSILON), 1.0 - LOG_LOSS_EPSILON)
        if clipped != probability:
            clamped += 1
        loss = -(label * math.log(clipped) + (1.0 - label) * math.log(1.0 - clipped))
        loss_rows.append((str(row["molecule_id"]), loss))
    log_loss = pooled_mean([value for _, value in loss_rows])
    log_loss_ci, log_loss_reason = _ci_or_reason(
        _cluster(loss_rows), seed=seed, n_resamples=n_resamples
    )
    bins: list[dict[str, Any]] = []
    for index in range(CALIBRATION_BINS):
        low = index / CALIBRATION_BINS
        high = (index + 1) / CALIBRATION_BINS
        members = [
            (probability, label)
            for probability, label in zip(probabilities, labels, strict=True)
            if min(int(probability * CALIBRATION_BINS), CALIBRATION_BINS - 1) == index
        ]
        bins.append(
            {
                "low": low,
                "high": high,
                "n": len(members),
                "mean_predicted": pooled_mean([p for p, _ in members]) if members else None,
                "observed_frequency": pooled_mean([label for _, label in members])
                if members
                else None,
            }
        )
    n_positive = sum(1 for label in labels if label == 1.0)
    reasons = sorted({reason for reason in (brier_reason, log_loss_reason) if reason})
    return {
        "status": "measured",
        "n_scored": len(rows),
        "n_positive": n_positive,
        "n_negative": len(rows) - n_positive,
        "prevalence": n_positive / len(rows),
        "brier": brier,
        "brier_ci": brier_ci,
        "log_loss": log_loss,
        "log_loss_ci": log_loss_ci,
        "clamped": clamped,
        "calibration": {
            "n_bins": CALIBRATION_BINS,
            "bin_edges": [index / CALIBRATION_BINS for index in range(CALIBRATION_BINS + 1)],
            "bins": bins,
        },
        "distribution_note": distribution_note,
        "reasons": reasons,
    }


def _true_structure_absence(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    absent = [record for record in records if not record["true_structure_present"]]
    if not absent:
        return {
            "status": "not_verified",
            "n_items": 0,
            "n_with_valid_candidates": 0,
            "n_refused": 0,
            "n_confident_winner": 0,
            "confident_winner_rate": None,
            "confidence_threshold": ABSENT_CONFIDENCE_THRESHOLD,
            "reasons": ["no_true_structure_absent_items"],
        }
    with_valid = [
        record
        for record in absent
        if record["absent_behavior"]["status"] == "true_structure_absent"
    ]
    confident = sum(1 for record in with_valid if record["absent_behavior"]["confident"])
    return {
        "status": "measured",
        "n_items": len(absent),
        "n_with_valid_candidates": len(with_valid),
        "n_refused": len(absent) - len(with_valid),
        "n_confident_winner": confident,
        "confident_winner_rate": confident / len(absent),
        "confidence_threshold": ABSENT_CONFIDENCE_THRESHOLD,
        "reasons": [],
    }


def _refusal(
    records: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    all_candidates = [candidate for item in items for candidate in item["candidates"]]
    by_status = Counter(
        candidate["status"] for candidate in all_candidates if candidate["status"] != "valid"
    )
    n_candidates = len(all_candidates)
    n_valid = sum(1 for candidate in all_candidates if candidate["status"] == "valid")
    refused_items = [record for record in records if record["status"] == "not_verified"]
    return {
        "status": "measured" if n_candidates else "not_verified",
        "n_candidates": n_candidates,
        "n_valid": n_valid,
        "n_nonvalid": n_candidates - n_valid,
        "refusal_rate": (n_candidates - n_valid) / n_candidates if n_candidates else None,
        "by_status": dict(sorted(by_status.items())),
        "n_items": len(records),
        "n_items_refused": len(refused_items),
        "item_refusal_rate": len(refused_items) / len(records) if records else None,
        "reasons": [] if n_candidates else ["no_candidates"],
    }


def evaluate_dataset(
    dataset: Mapping[str, Any],
    *,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
) -> dict[str, Any]:
    """Evaluate one validated dataset block into the canonical metric record."""
    items = list(dataset["items"])
    records: list[dict[str, Any]] = []
    contributions: list[dict[str, Any]] = []
    for item in items:
        record, contribution = _evaluate_item(item)
        records.append(record)
        contributions.append(contribution)

    distribution = dataset["distribution"]
    top1_rows: list[tuple[str, bool]] = []
    top1_excluded: list[str] = []
    top1_dp5_rows: list[tuple[str, bool]] = []
    top1_dp5_excluded: list[str] = []
    dp4_rows: list[dict[str, Any]] = []
    dp5_rows: list[dict[str, Any]] = []
    for record, contribution in zip(records, contributions, strict=True):
        if record["true_structure_present"]:
            if record["top1_is_true"] is None:
                top1_excluded.append(record["item_id"])
            else:
                top1_rows.append((record["molecule_id"], record["top1_is_true"]))
            if record["top1_dp5_is_true"] is None:
                top1_dp5_excluded.append(record["item_id"])
            else:
                top1_dp5_rows.append((record["molecule_id"], record["top1_dp5_is_true"]))
        for candidate in contribution["valid_candidates"]:
            label = 1.0 if candidate["is_true_structure"] else 0.0
            if candidate["dp4_probability"] is not None:
                dp4_rows.append(
                    {
                        "molecule_id": record["molecule_id"],
                        "probability": float(candidate["dp4_probability"]),
                        "label": label,
                    }
                )
            if candidate["dp5_probability"] is not None:
                dp5_rows.append(
                    {
                        "molecule_id": record["molecule_id"],
                        "probability": float(candidate["dp5_probability"]),
                        "label": label,
                    }
                )

    molecule_ids = sorted({record["molecule_id"] for record in records})
    return {
        "dataset_id": str(dataset["dataset_id"]),
        "title": str(dataset.get("title", "")),
        "layer": str(dataset["layer"]),
        "source": str(dataset["source"]),
        "license": str(dataset["license"]),
        "version": str(dataset["version"]),
        "hash": str(dataset["hash"]),
        "distribution": {
            "note": str(distribution["note"]),
            "balanced_by_construction": bool(distribution["balanced_by_construction"]),
        },
        "counts": {
            "n_items": len(records),
            "n_molecules": len(molecule_ids),
            "n_candidates": sum(len(item["candidates"]) for item in items),
            "n_valid_candidates": sum(
                1
                for item in items
                for candidate in item["candidates"]
                if candidate["status"] == "valid"
            ),
        },
        "shift_accuracy": _shift_accuracy(contributions, seed=seed, n_resamples=n_resamples),
        "assignment": _assignment_accuracy(contributions, seed=seed, n_resamples=n_resamples),
        "ranking": {
            "top1_dp4": _top1_block(
                top1_rows,
                excluded_item_ids=top1_excluded,
                seed=seed,
                n_resamples=n_resamples,
                empty_reason="no_rankable_true_structure_items",
            ),
            "top1_dp5": _top1_block(
                top1_dp5_rows,
                excluded_item_ids=top1_dp5_excluded,
                seed=seed,
                n_resamples=n_resamples,
                empty_reason="no_dp5_probability_data",
            ),
        },
        "binary_probability": {
            "dp4": _binary_probability(
                dp4_rows,
                distribution_note=str(distribution["note"]),
                seed=seed,
                n_resamples=n_resamples,
            ),
            "dp5": _binary_probability(
                dp5_rows,
                distribution_note=str(distribution["note"]),
                seed=seed,
                n_resamples=n_resamples,
            ),
        },
        "true_structure_absence": _true_structure_absence(records),
        "refusal": _refusal(records, items),
        "items": records,
    }


__all__ = [
    "ABSENT_CONFIDENCE_THRESHOLD",
    "CALIBRATION_BINS",
    "LOG_LOSS_EPSILON",
    "evaluate_dataset",
]
