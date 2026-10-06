"""ShiftPredictor screening-evaluation hooks (todo 57 / gap G17).

Wires a todo-55 ``ShiftPredictor`` (the todo-56 DP5q stub is one explicit
implementation) into the todo-50 benchmark framework as a
**screening / prioritization aid**, never as a DP5 replacement:

* **screening / priority ranking** — predictor outputs (uncertainty-normalized
  when a todo-55 distribution is available) order candidates BEFORE the
  expensive calculation chain; the evaluation reports whether the true
  structure survives the configured top-k screening cut;
* **hard / out-of-domain escalation** — candidates flagged uncertain or
  out-of-domain by the todo-57 support logic (todo-38 neighbour-support
  convention: out of domain iff ``effective_neighbors < 1``) are escalated to
  the DFT path as a TYPED DECISION RECORD; this harness never executes QC and
  every record carries ``executed: false``;
* **miss-risk metrics** — per-dataset false-drop rate (true structure not
  retained) plus a molecule-clustered bootstrap CI (todo-50
  ``clustered_bootstrap_ci``), the post-escalation ``unmitigated`` variant,
  and a speed/accuracy trade-off table (screening fraction vs retained-true
  rate vs cost proxy).  A true candidate that could not be scored counts as
  DROPPED (conservative), never as retained.

Honesty discipline (todo-54 conventions):

* the top-level ``claim`` states that this is a screening aid, NOT a DP5
  replacement and NOT evidence that DP5q is equivalent to DP5 (the
  structured ``scope`` block pins ``dp5_replacement=false`` /
  ``dp5_equivalence_claim=false``);
* conclusions are produced ONLY over a declared independent evaluation set
  (``evaluation_set`` block with source/license/version/hash and an
  independence note; ``kind="external_layer"`` anchors it in a todo-53
  loader-validated layer dataset).  Without such a set the hook returns a
  ``not_verified`` payload with explicit ``NOT_VERIFIED`` reasons and no
  numbers — never a conclusion.

The screening dataset manifest (``acp-nmr-shift-predictor-eval-dataset-v1``)
reuses the todo-50 typed provenance errors (``MissingProvenanceError`` /
``DatasetHashMismatchError`` / ``ManifestReferenceError``) and
``canonical_dataset_hash``; the output
(``acp-nmr-shift-predictor-eval-metrics-v1``) is canonical JSON via the
todo-48 ``canonical_json_bytes`` / ``write_metrics`` pair, byte-identical
across processes with a pinned seed/now.  No QC subprocess is started here,
and no predictor residual ever enters the Goodman error models.

Handoff: todo 58 (docs) references this module as the honest scope statement
for the DP5q screening route; the external evaluation campaign remains a
follow-up outside this plan.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from acp.nmr.dp5q_stub import Dp5qStubError
from acp.nmr.fchl import MIN_EFFECTIVE_NEIGHBORS
from acp.nmr.shift_predictor import (
    GeometryRequirements,
    InvalidShiftPredictorError,
    PredictorGeometry,
    ShiftModelProvenance,
    ShiftPrediction,
    ShiftPredictionRequest,
    ShiftPredictor,
    ShiftPredictorError,
)
from tests.benchmark.nmr.bootstrap import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_RESAMPLES,
    clustered_bootstrap_ci,
)
from tests.benchmark.nmr.harness import REPO_ROOT, _code_provenance
from tests.benchmark.nmr.loaders import BENCHMARK_LAYERS_2_5, load_layer_datasets
from tests.benchmark.nmr.schema import (
    BenchmarkManifestError,
    DatasetHashMismatchError,
    ManifestReferenceError,
    MissingProvenanceError,
    canonical_dataset_hash,
)
from tests.nmr_spectra_benchmark import NOT_VERIFIED, write_metrics

SCREENING_DATASET_SCHEMA: Final = "acp-nmr-shift-predictor-eval-dataset-v1"
SCREENING_EVAL_SCHEMA: Final = "acp-nmr-shift-predictor-eval-metrics-v1"

#: Closed vocabulary of evaluation-set declarations.
EVALUATION_SET_KINDS: Final[tuple[str, ...]] = ("synthetic_labeled", "external_layer")

#: Closed vocabulary of escalation reasons on a decision record.
ESCALATION_REASONS: Final[tuple[str, ...]] = (
    "support_out_of_domain",
    "uncertainty_missing",
    "residual_z_above_threshold",
    "predictor_refused",
    "no_signal_coverage",
)

DEFAULT_Z_THRESHOLD: Final = 2.0

SCREENING_AID_CLAIM: Final = (
    "screening/prioritization aid only — NOT a DP5 replacement, NOT evidence that "
    "DP5q is equivalent to DP5, and NOT an accuracy or calibration claim; conclusions "
    "are produced only over a declared independent evaluation set"
)

SCREENING_AID_SCOPE: Final[dict[str, Any]] = {
    "role": "screening_aid_priority_ranking",
    "dp5_replacement": False,
    "dp5_equivalence_claim": False,
    "executes_qc": False,
    "conclusions_require_independent_evaluation_set": True,
}

#: Typed predictor refusals the evaluation records instead of crashing.  Any
#: other exception is a real defect and propagates.
PREDICTOR_REFUSAL_TYPES: Final[tuple[type[BaseException], ...]] = (
    ShiftPredictorError,
    Dp5qStubError,
)

_GENERATOR: Final = "tests/benchmark/nmr/shift_predictor_eval.py"
_ESCALATION_NOTE: Final = (
    "DFT escalation is recorded as an audit decision; this harness never executes "
    "quantum-chemistry calculations"
)
_COST_PROXY_DEFINITION: Final = (
    "fraction of expensive (DFT) candidate evaluations remaining after the top-k "
    "screening cut plus escalated candidates outside the cut, over all candidates"
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EVALUATION_SET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "evaluation_set_id",
        "kind",
        "source",
        "license",
        "version",
        "hash",
        "independent_of_model",
        "independence_note",
        "distribution",
        "layer",
        "layer_manifest",
        "layer_dataset_hash",
    }
)
_SUPPORT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "n_train",
        "contributing_neighbors",
        "effective_neighbors",
        "support_fraction",
        "similarity_mass",
        "threshold",
        "out_of_domain",
    }
)


# ---------------------------------------------------------------------------
# typed errors
# ---------------------------------------------------------------------------


class ShiftPredictorEvalError(ValueError):
    """Base class for typed screening-evaluation misuse."""


class PredictorNotSuppliedError(ShiftPredictorEvalError):
    """No predictor was supplied — screening cannot be evaluated."""


class DatasetNotIndependentError(ShiftPredictorEvalError):
    """The evaluation set does not declare model independence (never guessed)."""


class InvalidScreeningCutError(ShiftPredictorEvalError):
    """The top-k screening cut is not a positive integer within candidate counts."""


class ScreeningManifestError(BenchmarkManifestError):
    """Structural violation of the screening dataset manifest."""


# ---------------------------------------------------------------------------
# typed records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreeningSignal:
    """One experimental signal the screening score is measured against."""

    signal_id: str
    observed_ppm: float

    def __post_init__(self) -> None:
        _require_text(self.signal_id, "ScreeningSignal.signal_id")
        object.__setattr__(
            self, "observed_ppm", _require_finite(self.observed_ppm, "ScreeningSignal.observed_ppm")
        )

    def as_dict(self) -> dict[str, Any]:
        return {"signal_id": self.signal_id, "observed_ppm": self.observed_ppm}


@dataclass(frozen=True)
class ScreeningSupport:
    """Todo-38-shaped neighbour support for one candidate (derived out-of-domain).

    ``out_of_domain`` is DERIVED from the todo-38 convention
    (``effective_neighbors < acp.nmr.fchl.MIN_EFFECTIVE_NEIGHBORS``); a manifest
    may restate the flag but a contradiction is rejected at load time.
    """

    n_train: int
    contributing_neighbors: int
    effective_neighbors: float
    support_fraction: float | None = None
    similarity_mass: float | None = None
    threshold: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.n_train, bool) or not isinstance(self.n_train, int):
            raise ScreeningManifestError("ScreeningSupport.n_train must be an int")
        if self.n_train < 0:
            raise ScreeningManifestError("ScreeningSupport.n_train must be >= 0")
        if isinstance(self.contributing_neighbors, bool) or not isinstance(
            self.contributing_neighbors, int
        ):
            raise ScreeningManifestError("ScreeningSupport.contributing_neighbors must be an int")
        if not 0 <= self.contributing_neighbors <= self.n_train:
            raise ScreeningManifestError(
                "ScreeningSupport.contributing_neighbors must be within [0, n_train]"
            )
        effective = _require_finite(
            self.effective_neighbors, "ScreeningSupport.effective_neighbors"
        )
        if effective < 0:
            raise ScreeningManifestError("ScreeningSupport.effective_neighbors must be >= 0")
        object.__setattr__(self, "effective_neighbors", effective)
        if self.support_fraction is not None:
            fraction = _require_finite(self.support_fraction, "ScreeningSupport.support_fraction")
            if not 0.0 <= fraction <= 1.0:
                raise ScreeningManifestError("ScreeningSupport.support_fraction must be in [0, 1]")
            object.__setattr__(self, "support_fraction", fraction)
        if self.similarity_mass is not None:
            mass = _require_finite(self.similarity_mass, "ScreeningSupport.similarity_mass")
            if mass < 0:
                raise ScreeningManifestError("ScreeningSupport.similarity_mass must be >= 0")
            object.__setattr__(self, "similarity_mass", mass)
        threshold = _require_finite(self.threshold, "ScreeningSupport.threshold")
        if threshold < 0:
            raise ScreeningManifestError("ScreeningSupport.threshold must be >= 0")
        object.__setattr__(self, "threshold", threshold)

    @property
    def out_of_domain(self) -> bool:
        """Todo-38 rule: no contributing training neighbour (effective < 1)."""
        return self.effective_neighbors < MIN_EFFECTIVE_NEIGHBORS

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_train": self.n_train,
            "contributing_neighbors": self.contributing_neighbors,
            "effective_neighbors": self.effective_neighbors,
            "support_fraction": self.support_fraction,
            "similarity_mass": self.similarity_mass,
            "threshold": self.threshold,
            "out_of_domain": self.out_of_domain,
        }


@dataclass(frozen=True)
class ScreeningEvaluationSet:
    """Declared independent evaluation set (provenance is never defaulted)."""

    evaluation_set_id: str
    kind: str
    source: str
    license: str
    version: str
    hash: str
    independent_of_model: bool
    independence_note: str
    distribution_note: str
    balanced_by_construction: bool
    layer: str | None = None
    layer_manifest: str | None = None
    layer_dataset_hash: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "evaluation_set_id",
            "source",
            "license",
            "version",
            "hash",
            "independence_note",
            "distribution_note",
        ):
            _require_text(getattr(self, field_name), f"ScreeningEvaluationSet.{field_name}")
        if self.kind not in EVALUATION_SET_KINDS:
            raise ScreeningManifestError(
                f"unknown evaluation set kind {self.kind!r}; expected one of {EVALUATION_SET_KINDS}"
            )
        if self.independent_of_model is not True:
            raise DatasetNotIndependentError(
                "the evaluation set must declare independent_of_model=true — labels used to "
                "fit, calibrate or select the predictor cannot produce screening conclusions"
            )
        if not isinstance(self.balanced_by_construction, bool):
            raise ScreeningManifestError(
                "ScreeningEvaluationSet.balanced_by_construction must be a bool"
            )
        if self.kind == "external_layer":
            if self.layer not in BENCHMARK_LAYERS_2_5:
                raise ScreeningManifestError(
                    f"an external_layer evaluation set needs layer in "
                    f"{BENCHMARK_LAYERS_2_5}, got {self.layer!r}"
                )
            _require_text(self.layer_manifest, "ScreeningEvaluationSet.layer_manifest")
            _require_text(self.layer_dataset_hash, "ScreeningEvaluationSet.layer_dataset_hash")
        else:
            if (
                self.layer is not None
                or self.layer_manifest is not None
                or self.layer_dataset_hash is not None
            ):
                raise ScreeningManifestError(
                    "a synthetic_labeled evaluation set must not carry layer anchor fields"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluation_set_id": self.evaluation_set_id,
            "kind": self.kind,
            "source": self.source,
            "license": self.license,
            "version": self.version,
            "hash": self.hash,
            "independent_of_model": self.independent_of_model,
            "independence_note": self.independence_note,
            "distribution": {
                "note": self.distribution_note,
                "balanced_by_construction": self.balanced_by_construction,
            },
            "layer": self.layer,
            "layer_manifest": self.layer_manifest,
            "layer_dataset_hash": self.layer_dataset_hash,
        }


@dataclass(frozen=True)
class ScreeningCandidate:
    """One candidate structure with its explicit predictor input mapping."""

    candidate_id: str
    is_true_structure: bool
    geometries: tuple[PredictorGeometry, ...]
    signal_atoms: Mapping[str, Mapping[str, str]]
    support: ScreeningSupport | None = None
    charge: int = 0
    multiplicity: int = 1

    def __post_init__(self) -> None:
        _require_text(self.candidate_id, "ScreeningCandidate.candidate_id")
        if not isinstance(self.is_true_structure, bool):
            raise ScreeningManifestError(
                f"ScreeningCandidate[{self.candidate_id}].is_true_structure must be a bool"
            )
        if not self.geometries:
            raise ScreeningManifestError(
                f"ScreeningCandidate[{self.candidate_id}] needs at least one predictor geometry"
            )
        reference = self.geometries[0].atom_uids
        for geometry in self.geometries[1:]:
            if geometry.atom_uids != reference:
                raise ScreeningManifestError(
                    f"ScreeningCandidate[{self.candidate_id}] geometries must share one atom "
                    "mapping"
                )
        if not self.signal_atoms:
            raise ScreeningManifestError(
                f"ScreeningCandidate[{self.candidate_id}] needs a signal_atoms mapping"
            )
        copied: dict[str, dict[str, str]] = {}
        for nucleus, mapping in self.signal_atoms.items():
            _require_text(nucleus, "ScreeningCandidate.signal_atoms key")
            if not isinstance(mapping, Mapping) or not mapping:
                raise ScreeningManifestError(
                    f"ScreeningCandidate[{self.candidate_id}].signal_atoms[{nucleus!r}] must be a "
                    "non-empty object"
                )
            entries: dict[str, str] = {}
            for signal_id, atom_uid in mapping.items():
                _require_text(signal_id, "ScreeningCandidate.signal_atoms signal id")
                _require_text(atom_uid, "ScreeningCandidate.signal_atoms atom uid")
                entries[signal_id] = atom_uid
            copied[nucleus] = entries
        object.__setattr__(self, "signal_atoms", copied)
        if isinstance(self.charge, bool) or not isinstance(self.charge, int):
            raise ScreeningManifestError(
                f"ScreeningCandidate[{self.candidate_id}].charge must be an int"
            )
        if (
            isinstance(self.multiplicity, bool)
            or not isinstance(self.multiplicity, int)
            or self.multiplicity < 1
        ):
            raise ScreeningManifestError(
                f"ScreeningCandidate[{self.candidate_id}].multiplicity must be an int >= 1"
            )


@dataclass(frozen=True)
class ScreeningItem:
    """One molecule item: experimental signals + candidates for the screen."""

    item_id: str
    molecule_id: str
    true_structure_present: bool
    true_structure_candidate_id: str | None
    experimental: Mapping[str, tuple[ScreeningSignal, ...]]
    candidates: tuple[ScreeningCandidate, ...]

    def __post_init__(self) -> None:
        _require_text(self.item_id, "ScreeningItem.item_id")
        _require_text(self.molecule_id, "ScreeningItem.molecule_id")
        if not isinstance(self.true_structure_present, bool):
            raise ScreeningManifestError(
                f"ScreeningItem[{self.item_id}] needs a bool presence flag"
            )
        if not self.candidates:
            raise ScreeningManifestError(f"ScreeningItem[{self.item_id}] carries no candidates")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ScreeningManifestError(
                f"ScreeningItem[{self.item_id}] candidate ids must be unique"
            )
        if not self.experimental:
            raise ScreeningManifestError(f"ScreeningItem[{self.item_id}] carries no signals")
        true_ids = [
            candidate.candidate_id for candidate in self.candidates if candidate.is_true_structure
        ]
        if self.true_structure_present:
            true_id = self.true_structure_candidate_id
            if true_id is None or true_id not in candidate_ids:
                raise ManifestReferenceError(
                    f"ScreeningItem[{self.item_id}]: true_structure_candidate_id {true_id!r} "
                    "does not resolve"
                )
            if true_ids != [true_id]:
                raise ScreeningManifestError(
                    f"ScreeningItem[{self.item_id}]: exactly the true candidate must carry "
                    "is_true_structure=true"
                )
        else:
            if self.true_structure_candidate_id is not None:
                raise ScreeningManifestError(
                    f"ScreeningItem[{self.item_id}]: an absent true structure must carry a null "
                    "true_structure_candidate_id"
                )
            if true_ids:
                raise ScreeningManifestError(
                    f"ScreeningItem[{self.item_id}]: no candidate may be marked true when the "
                    "structure is absent"
                )
        for candidate in self.candidates:
            _validate_signal_mapping(self, candidate)


@dataclass(frozen=True)
class ScreeningDataset:
    """Validated screening dataset: one declared set + its labeled items."""

    evaluation_set: ScreeningEvaluationSet
    items: tuple[ScreeningItem, ...]
    cut_k: int = 1
    z_threshold: float = DEFAULT_Z_THRESHOLD


@dataclass(frozen=True)
class _CandidateOutcome:
    """Internal per-candidate screening outcome (input to ranking/aggregation)."""

    candidate: ScreeningCandidate
    screen_status: str
    screen_score: float | None
    n_scored_signals: int
    n_uncovered_signals: int
    n_uncertainty_weighted_signals: int
    max_abs_residual_ppm: float | None
    max_z: float | None
    refusals: tuple[dict[str, Any], ...]
    escalation_reasons: tuple[str, ...]

    @property
    def escalated(self) -> bool:
        return bool(self.escalation_reasons)


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScreeningManifestError(f"{field_name} must be a non-blank string, got {value!r}")
    return value


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScreeningManifestError(f"{field_name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ScreeningManifestError(f"{field_name} must be finite, got {value!r}")
    return number


def _require_bool(mapping: Mapping[str, Any], key: str, where: str) -> bool:
    if key not in mapping:
        raise ScreeningManifestError(f"{where}: missing required field {key!r}")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ScreeningManifestError(f"{where}: {key!r} must be an explicit boolean")
    return value


def _require_key(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ScreeningManifestError(f"{where}: missing required field {key!r} (no defaults)")
    return mapping[key]


def _validate_signal_mapping(item: ScreeningItem, candidate: ScreeningCandidate) -> None:
    """Every experimental signal must map to an atom of the candidate geometry."""
    geometry_uids = set(candidate.geometries[0].atom_uids)
    for nucleus, mapping in candidate.signal_atoms.items():
        signals = item.experimental.get(nucleus)
        if not signals:
            raise ScreeningManifestError(
                f"ScreeningItem[{item.item_id}] candidate {candidate.candidate_id!r} maps "
                f"unknown nucleus {nucleus!r}"
            )
        known = {signal.signal_id for signal in signals}
        unknown = sorted(signal_id for signal_id in mapping if signal_id not in known)
        if unknown:
            raise ScreeningManifestError(
                f"ScreeningItem[{item.item_id}] candidate {candidate.candidate_id!r} maps "
                f"unknown signal ids {unknown}"
            )
        for signal_id, atom_uid in mapping.items():
            if atom_uid not in geometry_uids:
                raise ScreeningManifestError(
                    f"ScreeningItem[{item.item_id}] candidate {candidate.candidate_id!r} maps "
                    f"signal {signal_id!r} to atom uid {atom_uid!r} absent from its geometry"
                )
    for nucleus, signals in item.experimental.items():
        mapped = candidate.signal_atoms.get(nucleus, {})
        missing = [signal.signal_id for signal in signals if signal.signal_id not in mapped]
        if missing:
            raise ScreeningManifestError(
                f"ScreeningItem[{item.item_id}] candidate {candidate.candidate_id!r} does not map "
                f"signals {missing} for nucleus {nucleus!r}"
            )


def _validate_cut_k(cut_k: object, max_candidates: int) -> int:
    if isinstance(cut_k, bool) or not isinstance(cut_k, int):
        raise InvalidScreeningCutError(f"cut_k must be an integer >= 1, got {cut_k!r}")
    if cut_k < 1:
        raise InvalidScreeningCutError(f"cut_k must be >= 1, got {cut_k}")
    if cut_k > max_candidates:
        raise InvalidScreeningCutError(
            f"cut_k {cut_k} exceeds the largest candidate count {max_candidates}"
        )
    return cut_k


# ---------------------------------------------------------------------------
# manifest parsing
# ---------------------------------------------------------------------------


def _parse_support(payload: Any, where: str) -> ScreeningSupport:
    if not isinstance(payload, Mapping):
        raise ScreeningManifestError(f"{where}.support must be a JSON object")
    unknown = sorted(str(key) for key in payload if key not in _SUPPORT_KEYS)
    if unknown:
        raise ScreeningManifestError(f"{where}.support has unknown fields: {unknown}")
    support = ScreeningSupport(
        n_train=_require_key(payload, "n_train", f"{where}.support"),
        contributing_neighbors=_require_key(payload, "contributing_neighbors", f"{where}.support"),
        effective_neighbors=_require_key(payload, "effective_neighbors", f"{where}.support"),
        support_fraction=payload.get("support_fraction"),
        similarity_mass=payload.get("similarity_mass"),
        threshold=payload.get("threshold", 0.0),
    )
    declared = payload.get("out_of_domain")
    if declared is not None:
        if not isinstance(declared, bool):
            raise ScreeningManifestError(f"{where}.support.out_of_domain must be a bool")
        if declared != support.out_of_domain:
            raise ScreeningManifestError(
                f"{where}.support declares out_of_domain={declared} but the todo-38 rule "
                f"(effective_neighbors={support.effective_neighbors} < "
                f"{MIN_EFFECTIVE_NEIGHBORS}) derives {support.out_of_domain}"
            )
    return support


def _parse_geometry(payload: Any, where: str) -> PredictorGeometry:
    if not isinstance(payload, Mapping):
        raise ScreeningManifestError(f"{where} must be a JSON object")
    unknown = sorted(
        str(key) for key in payload if key not in {"symbols", "coordinates", "atom_uids", "level"}
    )
    if unknown:
        raise ScreeningManifestError(f"{where} has unknown fields: {unknown}")
    try:
        return PredictorGeometry(
            symbols=tuple(_require_key(payload, "symbols", where)),
            coordinates=tuple(_require_key(payload, "coordinates", where)),
            atom_uids=tuple(_require_key(payload, "atom_uids", where)),
            level=str(payload.get("level", "unknown")),
        )
    except (TypeError, ValueError) as exc:
        raise ScreeningManifestError(f"{where} is not a valid predictor geometry: {exc}") from exc


def _parse_candidate(payload: Any, where: str) -> ScreeningCandidate:
    if not isinstance(payload, Mapping):
        raise ScreeningManifestError(f"{where} must be a JSON object")
    candidate_id = _require_text(
        _require_key(payload, "candidate_id", where), f"{where}.candidate_id"
    )
    is_true = _require_bool(payload, "is_true_structure", where)
    geometries_payload = _require_key(payload, "predictor_geometries", where)
    if not isinstance(geometries_payload, list) or not geometries_payload:
        raise ScreeningManifestError(f"{where}.predictor_geometries must be a non-empty list")
    geometries = tuple(
        _parse_geometry(geometry, f"{where}.predictor_geometries[{index}]")
        for index, geometry in enumerate(geometries_payload)
    )
    signal_atoms_payload = _require_key(payload, "signal_atoms", where)
    if not isinstance(signal_atoms_payload, Mapping) or not signal_atoms_payload:
        raise ScreeningManifestError(f"{where}.signal_atoms must be a non-empty object")
    signal_atoms: dict[str, dict[str, str]] = {}
    for nucleus, mapping in signal_atoms_payload.items():
        if not isinstance(mapping, Mapping) or not mapping:
            raise ScreeningManifestError(
                f"{where}.signal_atoms[{nucleus!r}] must be a non-empty object"
            )
        signal_atoms[str(nucleus)] = {
            str(signal_id): str(atom_uid) for signal_id, atom_uid in mapping.items()
        }
    support_payload = payload.get("support")
    support = _parse_support(support_payload, where) if support_payload is not None else None
    return ScreeningCandidate(
        candidate_id=candidate_id,
        is_true_structure=is_true,
        geometries=geometries,
        signal_atoms=signal_atoms,
        support=support,
        charge=payload.get("charge", 0),
        multiplicity=payload.get("multiplicity", 1),
    )


def _parse_item(payload: Any, index: int) -> ScreeningItem:
    where = f"items[{index}]"
    if not isinstance(payload, Mapping):
        raise ScreeningManifestError(f"{where} must be a JSON object")
    item_id = _require_text(_require_key(payload, "item_id", where), f"{where}.item_id")
    molecule_id = _require_text(_require_key(payload, "molecule_id", where), f"{where}.molecule_id")
    present = _require_bool(payload, "true_structure_present", where)
    if "true_structure_candidate_id" not in payload:
        raise ScreeningManifestError(
            f"{where}: missing required field 'true_structure_candidate_id' (no defaults)"
        )
    true_id = payload["true_structure_candidate_id"]
    if true_id is not None and (not isinstance(true_id, str) or not true_id.strip()):
        raise ScreeningManifestError(
            f"{where}.true_structure_candidate_id must be null or a non-blank string"
        )
    experimental_payload = _require_key(payload, "experimental", where)
    if not isinstance(experimental_payload, Mapping) or not experimental_payload:
        raise ScreeningManifestError(f"{where}.experimental must be a non-empty object")
    experimental: dict[str, tuple[ScreeningSignal, ...]] = {}
    for nucleus, signals_payload in experimental_payload.items():
        nucleus_label = _require_text(nucleus, f"{where}.experimental nucleus")
        if not isinstance(signals_payload, list) or not signals_payload:
            raise ScreeningManifestError(
                f"{where}.experimental[{nucleus_label!r}] must be a non-empty list"
            )
        signals: list[ScreeningSignal] = []
        for signal_index, signal_payload in enumerate(signals_payload):
            signal_where = f"{where}.experimental[{nucleus_label!r}][{signal_index}]"
            if not isinstance(signal_payload, Mapping):
                raise ScreeningManifestError(f"{signal_where} must be a JSON object")
            signal_id = _require_text(
                _require_key(signal_payload, "signal_id", signal_where),
                f"{signal_where}.signal_id",
            )
            signals.append(
                ScreeningSignal(
                    signal_id=signal_id,
                    observed_ppm=_require_key(signal_payload, "observed_ppm", signal_where),
                )
            )
        seen = [signal.signal_id for signal in signals]
        if len(set(seen)) != len(seen):
            raise ScreeningManifestError(
                f"{where}.experimental[{nucleus_label!r}] signal ids must be unique"
            )
        experimental[nucleus_label] = tuple(signals)
    candidates_payload = _require_key(payload, "candidates", where)
    if not isinstance(candidates_payload, list) or not candidates_payload:
        raise ScreeningManifestError(f"{where}.candidates must be a non-empty list")
    candidates = tuple(
        _parse_candidate(candidate, f"{where}.candidates[{candidate_index}]")
        for candidate_index, candidate in enumerate(candidates_payload)
    )
    return ScreeningItem(
        item_id=item_id,
        molecule_id=molecule_id,
        true_structure_present=present,
        true_structure_candidate_id=true_id,
        experimental=experimental,
        candidates=candidates,
    )


def _parse_evaluation_set(payload: Any) -> ScreeningEvaluationSet:
    if not isinstance(payload, Mapping):
        raise ScreeningManifestError(
            "the screening manifest must declare an evaluation_set object "
            "(conclusions require a declared independent evaluation set)"
        )
    unknown = sorted(str(key) for key in payload if key not in _EVALUATION_SET_KEYS)
    if unknown:
        raise ScreeningManifestError(f"evaluation_set has unknown fields: {unknown}")
    distribution = _require_key(payload, "distribution", "evaluation_set")
    if not isinstance(distribution, Mapping):
        raise ScreeningManifestError("evaluation_set.distribution must be a JSON object")
    for key in ("evaluation_set_id", "kind", "source", "license", "version", "hash"):
        if key not in payload or not isinstance(payload[key], str) or not payload[key].strip():
            raise MissingProvenanceError(
                f"evaluation_set is missing required provenance field {key!r} (no defaults)"
            )
    hash_value = str(payload["hash"])
    if not _SHA256_RE.match(hash_value):
        raise ScreeningManifestError(
            "evaluation_set.hash must be a 64-char lowercase sha256 hex digest"
        )
    independent = payload.get("independent_of_model")
    if independent is not True:
        raise DatasetNotIndependentError(
            "the evaluation set must declare independent_of_model=true — a set without that "
            "declaration (or with false) can never produce screening conclusions"
        )
    independence_note = payload.get("independence_note")
    if not isinstance(independence_note, str) or not independence_note.strip():
        raise DatasetNotIndependentError(
            "the evaluation set must carry a non-blank independence_note stating why labels "
            "were not used to fit/calibrate/select the predictor"
        )
    return ScreeningEvaluationSet(
        evaluation_set_id=str(payload["evaluation_set_id"]),
        kind=str(payload["kind"]),
        source=str(payload["source"]),
        license=str(payload["license"]),
        version=str(payload["version"]),
        hash=hash_value,
        independent_of_model=True,
        independence_note=independence_note,
        distribution_note=_require_key(distribution, "note", "evaluation_set.distribution"),
        balanced_by_construction=_require_bool(
            distribution, "balanced_by_construction", "evaluation_set.distribution"
        ),
        layer=payload.get("layer"),
        layer_manifest=payload.get("layer_manifest"),
        layer_dataset_hash=payload.get("layer_dataset_hash"),
    )


def _validate_external_anchor(
    evaluation_set: ScreeningEvaluationSet, items: tuple[ScreeningItem, ...]
) -> None:
    manifest_ref = str(evaluation_set.layer_manifest)
    path = Path(manifest_ref)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        datasets = load_layer_datasets(path)
    except OSError as exc:
        raise ScreeningManifestError(
            f"external layer manifest {manifest_ref!r} is unreadable: {exc}"
        ) from exc
    matches = [
        dataset for dataset in datasets if dataset.dataset_id == evaluation_set.evaluation_set_id
    ]
    if not matches:
        raise ScreeningManifestError(
            f"external layer manifest does not contain dataset {evaluation_set.evaluation_set_id!r}"
        )
    layer = matches[0]
    if layer.layer != evaluation_set.layer:
        raise ScreeningManifestError(
            f"external layer anchor declares layer {evaluation_set.layer!r} but the loaded "
            f"dataset is {layer.layer!r}"
        )
    if layer.content_hash != evaluation_set.layer_dataset_hash:
        raise DatasetHashMismatchError(
            f"external layer dataset hash mismatch for {layer.dataset_id!r}: declared "
            f"{evaluation_set.layer_dataset_hash!r}, loaded {layer.content_hash!r}"
        )
    if layer.version != evaluation_set.version:
        raise ScreeningManifestError(
            f"external layer dataset version mismatch: declared {evaluation_set.version!r}, "
            f"loaded {layer.version!r}"
        )
    item_ids = {item.item_id for item in items}
    if item_ids != set(layer.item_ids):
        raise ScreeningManifestError(
            "screening items must correspond to the external layer dataset items; "
            f"expected {sorted(layer.item_ids)}, got {sorted(item_ids)}"
        )
    molecule_map = {item.item_id: item.molecule_id for item in items}
    expected_molecules = dict(zip(layer.item_ids, layer.molecule_ids))
    if molecule_map != expected_molecules:
        raise ScreeningManifestError(
            "screening item molecule ids must match the external layer dataset clusters"
        )


def load_screening_manifest(
    source: str | Path | Mapping[str, Any],
) -> ScreeningDataset:
    """Load and fully validate a screening dataset manifest.

    Args:
        source: Manifest path or an already-parsed JSON mapping.

    Raises:
        MissingProvenanceError / DatasetHashMismatchError / ManifestReferenceError:
            todo-50 provenance/reference violations, reused unchanged.
        ScreeningManifestError: structural violation of the screening schema.
        DatasetNotIndependentError: the set does not declare model independence.
    """
    if isinstance(source, Mapping):
        payload: Any = source
    else:
        path = Path(source)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ScreeningManifestError(f"cannot read screening manifest {path}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScreeningManifestError(
                f"screening manifest {path} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(payload, Mapping):
        raise ScreeningManifestError("screening manifest must be a JSON object")
    if payload.get("schema") != SCREENING_DATASET_SCHEMA:
        raise ScreeningManifestError(
            f"unsupported screening manifest schema {payload.get('schema')!r}; expected "
            f"{SCREENING_DATASET_SCHEMA!r}"
        )
    evaluation_set = _parse_evaluation_set(payload.get("evaluation_set"))
    items_payload = payload.get("items")
    if not isinstance(items_payload, list) or not items_payload:
        raise ScreeningManifestError("screening manifest carries no items")
    actual_hash = canonical_dataset_hash(items_payload)
    if evaluation_set.hash != actual_hash:
        raise DatasetHashMismatchError(
            f"evaluation_set.hash {evaluation_set.hash!r} does not match the canonical items "
            f"hash {actual_hash!r}"
        )
    items = tuple(_parse_item(item, index) for index, item in enumerate(items_payload))
    max_candidates = max(len(item.candidates) for item in items)
    screening_payload = payload.get("screening", {})
    if not isinstance(screening_payload, Mapping):
        raise ScreeningManifestError("screening must be a JSON object when present")
    unknown = sorted(str(key) for key in screening_payload if key not in {"cut_k", "z_threshold"})
    if unknown:
        raise ScreeningManifestError(f"screening has unknown fields: {unknown}")
    cut_k = _validate_cut_k(screening_payload.get("cut_k", 1), max_candidates)
    z_threshold = _require_finite(
        screening_payload.get("z_threshold", DEFAULT_Z_THRESHOLD), "screening.z_threshold"
    )
    if z_threshold <= 0:
        raise ScreeningManifestError("screening.z_threshold must be > 0")
    if evaluation_set.kind == "external_layer":
        _validate_external_anchor(evaluation_set, items)
    return ScreeningDataset(
        evaluation_set=evaluation_set, items=items, cut_k=cut_k, z_threshold=z_threshold
    )


# ---------------------------------------------------------------------------
# screening core
# ---------------------------------------------------------------------------


def _predictor_receipt(predictor: ShiftPredictor) -> dict[str, Any]:
    provenance = predictor.model_provenance
    geometry = predictor.geometry_requirements
    if not isinstance(provenance, ShiftModelProvenance):
        raise ShiftPredictorEvalError(
            "predictor.model_provenance must be a ShiftModelProvenance record, got "
            f"{type(provenance).__name__}"
        )
    if not isinstance(geometry, GeometryRequirements):
        raise ShiftPredictorEvalError(
            "predictor.geometry_requirements must be a GeometryRequirements record, got "
            f"{type(geometry).__name__}"
        )
    return {
        "model_provenance": provenance.as_dict(),
        "geometry_requirements": geometry.as_dict(),
    }


def _screen_candidate(
    predictor: ShiftPredictor,
    item: ScreeningItem,
    candidate: ScreeningCandidate,
    *,
    z_threshold: float,
) -> _CandidateOutcome:
    signal_scores: list[float] = []
    abs_residuals: list[float] = []
    z_values: list[float] = []
    n_uncovered = 0
    refusals: list[dict[str, Any]] = []
    for nucleus in sorted(item.experimental):
        signals = item.experimental[nucleus]
        mapping = candidate.signal_atoms[nucleus]
        request = ShiftPredictionRequest(
            nucleus=nucleus,
            geometries=candidate.geometries,
            charge=candidate.charge,
            multiplicity=candidate.multiplicity,
        )
        try:
            prediction = predictor.predict(request)
        except PREDICTOR_REFUSAL_TYPES as exc:
            refusals.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "nucleus": nucleus,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            n_uncovered += len(signals)
            continue
        if not isinstance(prediction, ShiftPrediction):
            raise ShiftPredictorEvalError(
                f"predictor returned {type(prediction).__name__}, expected a ShiftPrediction"
            )
        for signal in signals:
            predicted = prediction.shifts.get(mapping[signal.signal_id])
            if predicted is None:
                n_uncovered += 1
                continue
            abs_residual = abs(predicted.shift_ppm - signal.observed_ppm)
            abs_residuals.append(abs_residual)
            distribution = predicted.distribution
            std = distribution.std_ppm if distribution is not None else None
            if std is not None and std > 0.0:
                z_value = abs_residual / std
                z_values.append(z_value)
                signal_scores.append(z_value)
            else:
                signal_scores.append(abs_residual)
    if signal_scores:
        screen_status = "scored"
        screen_score: float | None = math.fsum(signal_scores) / len(signal_scores)
    elif refusals:
        screen_status = "predictor_refused"
        screen_score = None
    else:
        screen_status = "uncovered"
        screen_score = None
    reasons: list[str] = []
    if candidate.support is not None and candidate.support.out_of_domain:
        reasons.append("support_out_of_domain")
    if len(z_values) < len(signal_scores):
        reasons.append("uncertainty_missing")
    max_z = max(z_values) if z_values else None
    if max_z is not None and max_z > z_threshold:
        reasons.append("residual_z_above_threshold")
    if refusals:
        reasons.append("predictor_refused")
    elif not signal_scores:
        reasons.append("no_signal_coverage")
    return _CandidateOutcome(
        candidate=candidate,
        screen_status=screen_status,
        screen_score=screen_score,
        n_scored_signals=len(signal_scores),
        n_uncovered_signals=n_uncovered,
        n_uncertainty_weighted_signals=len(z_values),
        max_abs_residual_ppm=max(abs_residuals) if abs_residuals else None,
        max_z=max_z,
        refusals=tuple(refusals),
        escalation_reasons=tuple(reasons),
    )


def _rank(outcomes: list[_CandidateOutcome]) -> list[_CandidateOutcome]:
    return sorted(
        outcomes,
        key=lambda outcome: (
            outcome.screen_score is None,
            outcome.screen_score if outcome.screen_score is not None else 0.0,
            outcome.candidate.candidate_id,
        ),
    )


def _ranking_entries(ordered: list[_CandidateOutcome]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "candidate_id": outcome.candidate.candidate_id,
            "is_true_structure": outcome.candidate.is_true_structure,
            "screen_status": outcome.screen_status,
            "screen_score": outcome.screen_score,
            "n_scored_signals": outcome.n_scored_signals,
            "n_uncovered_signals": outcome.n_uncovered_signals,
            "n_uncertainty_weighted_signals": outcome.n_uncertainty_weighted_signals,
            "max_abs_residual_ppm": outcome.max_abs_residual_ppm,
            "max_z": outcome.max_z,
            "escalated": outcome.escalated,
            "escalation_reasons": list(outcome.escalation_reasons),
        }
        for rank, outcome in enumerate(ordered, start=1)
    ]


def _evaluate_item(
    predictor: ShiftPredictor,
    item: ScreeningItem,
    *,
    cut_k: int,
    z_threshold: float,
) -> tuple[dict[str, Any], list[_CandidateOutcome]]:
    outcomes = [
        _screen_candidate(predictor, item, candidate, z_threshold=z_threshold)
        for candidate in item.candidates
    ]
    ordered = _rank(outcomes)
    scored = any(outcome.screen_score is not None for outcome in outcomes)
    status = "measured" if scored else "not_verified"
    reasons = (
        []
        if scored
        else [
            f"{NOT_VERIFIED}: no candidate in item {item.item_id!r} could be scored by the "
            "predictor"
        ]
    )
    if item.true_structure_present:
        true_id = str(item.true_structure_candidate_id)
        true_outcome = next(
            outcome for outcome in outcomes if outcome.candidate.candidate_id == true_id
        )
        true_rank = [outcome.candidate.candidate_id for outcome in ordered].index(true_id) + 1
        true_scored = true_outcome.screen_score is not None
        true_survives = true_scored and true_rank <= cut_k
        false_drop = not true_survives
        unmitigated = false_drop and not true_outcome.escalated
    else:
        true_rank = None
        true_survives = None
        false_drop = None
        unmitigated = None
    record = {
        "item_id": item.item_id,
        "molecule_id": item.molecule_id,
        "true_structure_present": item.true_structure_present,
        "true_structure_candidate_id": item.true_structure_candidate_id,
        "n_candidates": len(item.candidates),
        "status": status,
        "reasons": reasons,
        "ranking": _ranking_entries(ordered),
        "true_rank": true_rank,
        "true_screen_status": (true_outcome.screen_status if item.true_structure_present else None),
        "true_survives_top_k": true_survives,
        "false_drop": false_drop,
        "unmitigated_false_drop": unmitigated,
        "predictor_refusals": [refusal for outcome in outcomes for refusal in outcome.refusals],
    }
    return record, ordered


def _rate_clusters(records: list[dict[str, Any]], field: str) -> dict[str, list[float]]:
    clusters: dict[str, list[float]] = {}
    for record in records:
        if record["true_structure_present"]:
            clusters.setdefault(str(record["molecule_id"]), []).append(
                1.0 if record[field] else 0.0
            )
    return clusters


def _ci_block(
    clusters: dict[str, list[float]], *, n_resamples: int, seed: int
) -> tuple[float, dict[str, Any]]:
    indicators = [value for values in clusters.values() for value in values]
    rate = math.fsum(indicators) / len(indicators)
    interval = clustered_bootstrap_ci(clusters, n_resamples=n_resamples, seed=seed)
    return rate, interval.as_dict()


def _miss_risk_block(
    item_records: list[dict[str, Any]],
    *,
    n_resamples: int,
    seed: int,
) -> dict[str, Any]:
    true_records = [record for record in item_records if record["true_structure_present"]]
    if not true_records:
        return {
            "status": "not_verified",
            "reasons": [
                f"{NOT_VERIFIED}: no item carries a true structure — false-drop risk cannot be "
                "measured"
            ],
            "false_drop_rate": None,
            "n_items_with_true": 0,
            "n_dropped": 0,
            "ci": None,
            "unmitigated_false_drop_rate": None,
            "n_unmitigated_dropped": 0,
            "unmitigated_ci": None,
        }
    drop_clusters = _rate_clusters(item_records, "false_drop")
    unmitigated_clusters = _rate_clusters(item_records, "unmitigated_false_drop")
    false_drop_rate, ci = _ci_block(drop_clusters, n_resamples=n_resamples, seed=seed)
    unmitigated_rate, unmitigated_ci = _ci_block(
        unmitigated_clusters, n_resamples=n_resamples, seed=seed
    )
    return {
        "status": "measured",
        "reasons": [],
        "false_drop_rate": false_drop_rate,
        "n_items_with_true": len(true_records),
        "n_dropped": sum(1 for record in true_records if record["false_drop"]),
        "ci": ci,
        "unmitigated_false_drop_rate": unmitigated_rate,
        "n_unmitigated_dropped": sum(
            1 for record in true_records if record["unmitigated_false_drop"]
        ),
        "unmitigated_ci": unmitigated_ci,
    }


def _escalation_block(item_records: list[dict[str, Any]], *, z_threshold: float) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for record in item_records:
        for entry in record["ranking"]:
            escalated = bool(entry["escalated"])
            decisions.append(
                {
                    "item_id": record["item_id"],
                    "molecule_id": record["molecule_id"],
                    "candidate_id": entry["candidate_id"],
                    "screen_rank": entry["rank"],
                    "screen_score": entry["screen_score"],
                    "escalated": escalated,
                    "reasons": list(entry["escalation_reasons"]),
                    "action": (
                        "escalate_to_dft_path" if escalated else "proceed_with_screening_aid"
                    ),
                    "executed": False,
                    "note": _ESCALATION_NOTE,
                    "criteria": {
                        "z_threshold": z_threshold,
                        "min_effective_neighbors": MIN_EFFECTIVE_NEIGHBORS,
                    },
                }
            )
    return {
        "status": "measured",
        "reasons": [],
        "z_threshold": z_threshold,
        "min_effective_neighbors": MIN_EFFECTIVE_NEIGHBORS,
        "n_candidates": len(decisions),
        "n_escalated": sum(1 for decision in decisions if decision["escalated"]),
        "executed": False,
        "decisions": decisions,
    }


def _tradeoff_block(
    item_records: list[dict[str, Any]],
    *,
    cut_k: int,
) -> dict[str, Any]:
    true_records = [record for record in item_records if record["true_structure_present"]]
    if not true_records:
        return {
            "status": "not_verified",
            "reasons": [
                f"{NOT_VERIFIED}: no item carries a true structure — retained-true rates "
                "cannot be measured"
            ],
            "cost_proxy_definition": _COST_PROXY_DEFINITION,
            "rows": [],
        }
    total_candidates = sum(record["n_candidates"] for record in item_records)
    max_candidates = max(record["n_candidates"] for record in item_records)
    rows: list[dict[str, Any]] = []
    for k in range(1, max_candidates + 1):
        kept = sum(min(k, record["n_candidates"]) for record in item_records)
        escalated_outside = sum(
            1
            for record in item_records
            for entry in record["ranking"]
            if entry["escalated"] and entry["rank"] > k
        )
        retained = sum(
            1
            for record in true_records
            if record["true_screen_status"] == "scored"
            and record["true_rank"] is not None
            and record["true_rank"] <= k
        )
        rows.append(
            {
                "k": k,
                "primary": k == cut_k,
                "n_items_with_true": len(true_records),
                "n_true_retained": retained,
                "retained_true_rate": retained / len(true_records),
                "false_drop_rate": (len(true_records) - retained) / len(true_records),
                "screening_fraction": 1.0 - kept / total_candidates,
                "cost_proxy": (kept + escalated_outside) / total_candidates,
            }
        )
    return {
        "status": "measured",
        "reasons": [],
        "cost_proxy_definition": _COST_PROXY_DEFINITION,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# top-level entry points
# ---------------------------------------------------------------------------


def _empty_screening_block(reason: str) -> dict[str, Any]:
    def block() -> dict[str, Any]:
        return {"status": "not_verified", "reasons": [reason]}

    return {
        "status": "not_verified",
        "reasons": [reason],
        "cut_k": None,
        "z_threshold": None,
        "miss_risk": {
            **block(),
            "false_drop_rate": None,
            "n_items_with_true": 0,
            "n_dropped": 0,
            "ci": None,
            "unmitigated_false_drop_rate": None,
            "n_unmitigated_dropped": 0,
            "unmitigated_ci": None,
        },
        "escalation": {
            **block(),
            "z_threshold": None,
            "min_effective_neighbors": MIN_EFFECTIVE_NEIGHBORS,
            "n_candidates": 0,
            "n_escalated": 0,
            "executed": False,
            "decisions": [],
        },
        "tradeoff": {
            **block(),
            "cost_proxy_definition": _COST_PROXY_DEFINITION,
            "rows": [],
        },
        "items": [],
    }


def run_screening_evaluation(
    predictor: ShiftPredictor | None,
    dataset: str | Path | Mapping[str, Any] | ScreeningDataset | None = None,
    *,
    cut_k: int | None = None,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    now: str | None = None,
) -> dict[str, Any]:
    """Run the screening evaluation and return the canonical metrics JSON.

    Args:
        predictor: Todo-55 ``ShiftPredictor``; ``None`` raises
            :class:`PredictorNotSuppliedError` (misuse, never a silent skip).
        dataset: Validated screening manifest (path, mapping or record).  When
            ``None`` the hook returns a ``not_verified`` payload with explicit
            reasons — no conclusion is ever produced without a declared
            independent evaluation set.
        cut_k: Top-k screening cut; ``None`` uses the manifest value (default
            1).  Invalid values raise :class:`InvalidScreeningCutError`.
        n_resamples: Molecule-clustered bootstrap resample count.
        seed: Bootstrap seed.
        now: ISO timestamp to record; pin with ``n_resamples`` for
            byte-identical replays.

    The DFT escalation path is only RECORDED (``executed=false``); this
    function never starts a QC subprocess.
    """
    if predictor is None:
        raise PredictorNotSuppliedError(
            "a ShiftPredictor instance is required for screening evaluation (none supplied)"
        )
    if not isinstance(predictor, ShiftPredictor):
        raise InvalidShiftPredictorError(
            "the supplied object does not satisfy the ShiftPredictor protocol "
            "(model_provenance / geometry_requirements / predict)"
        )
    timestamp = now if now is not None else datetime.now(timezone.utc).isoformat(timespec="seconds")
    receipt = {
        "schema": SCREENING_EVAL_SCHEMA,
        "claim": SCREENING_AID_CLAIM,
        "scope": dict(SCREENING_AID_SCOPE),
        "predictor": _predictor_receipt(predictor),
    }
    provenance = {
        "generator": _GENERATOR,
        "timestamp": timestamp,
        "seed": seed,
        "n_resamples": n_resamples,
        "bootstrap_unit": "molecule",
        "code": _code_provenance(),
    }
    if dataset is None:
        reason = (
            f"{NOT_VERIFIED}: no declared independent evaluation set was supplied; the screening "
            "hook produces no conclusion without one"
        )
        screening = _empty_screening_block(reason)
        return {
            **receipt,
            "evaluation_set": None,
            "screening": screening,
            "provenance": provenance,
            "summary": {
                "n_items": 0,
                "n_candidates": 0,
                "n_items_with_true": 0,
                "n_escalated": 0,
                "n_scored_candidates": 0,
                "block_statuses": {
                    "screening": screening["status"],
                    "miss_risk": screening["miss_risk"]["status"],
                    "escalation": screening["escalation"]["status"],
                    "tradeoff": screening["tradeoff"]["status"],
                },
            },
        }
    resolved = (
        dataset if isinstance(dataset, ScreeningDataset) else load_screening_manifest(dataset)
    )
    max_candidates = max(len(item.candidates) for item in resolved.items)
    resolved_cut = _validate_cut_k(resolved.cut_k if cut_k is None else cut_k, max_candidates)
    item_records: list[dict[str, Any]] = []
    outcomes: list[_CandidateOutcome] = []
    for item in resolved.items:
        record, ordered = _evaluate_item(
            predictor, item, cut_k=resolved_cut, z_threshold=resolved.z_threshold
        )
        item_records.append(record)
        outcomes.extend(ordered)
    scored_candidates = [outcome for outcome in outcomes if outcome.screen_score is not None]
    screening_status = "measured" if scored_candidates else "not_verified"
    screening_reasons = (
        []
        if scored_candidates
        else [
            f"{NOT_VERIFIED}: no candidate could be scored by the predictor — no screening "
            "ranking is produced"
        ]
    )
    screening = {
        "status": screening_status,
        "reasons": screening_reasons,
        "cut_k": resolved_cut,
        "z_threshold": resolved.z_threshold,
        "miss_risk": _miss_risk_block(item_records, n_resamples=n_resamples, seed=seed),
        "escalation": _escalation_block(item_records, z_threshold=resolved.z_threshold),
        "tradeoff": _tradeoff_block(item_records, cut_k=resolved_cut),
        "items": item_records,
    }
    return {
        **receipt,
        "evaluation_set": resolved.evaluation_set.as_dict(),
        "screening": screening,
        "provenance": provenance,
        "summary": {
            "n_items": len(item_records),
            "n_candidates": len(outcomes),
            "n_items_with_true": sum(
                1 for record in item_records if record["true_structure_present"]
            ),
            "n_escalated": screening["escalation"]["n_escalated"],
            "n_scored_candidates": len(scored_candidates),
            "block_statuses": {
                "screening": screening["status"],
                "miss_risk": screening["miss_risk"]["status"],
                "escalation": screening["escalation"]["status"],
                "tradeoff": screening["tradeoff"]["status"],
            },
        },
    }


def write_screening_evaluation(payload: Mapping[str, Any], path: str | Path) -> Path:
    """Canonically and atomically persist the payload; returns the written path."""
    return write_metrics(payload, path)


__all__ = [
    "DEFAULT_Z_THRESHOLD",
    "ESCALATION_REASONS",
    "EVALUATION_SET_KINDS",
    "PREDICTOR_REFUSAL_TYPES",
    "SCREENING_AID_CLAIM",
    "SCREENING_AID_SCOPE",
    "SCREENING_DATASET_SCHEMA",
    "SCREENING_EVAL_SCHEMA",
    "DatasetNotIndependentError",
    "InvalidScreeningCutError",
    "PredictorNotSuppliedError",
    "ScreeningCandidate",
    "ScreeningDataset",
    "ScreeningEvaluationSet",
    "ScreeningItem",
    "ScreeningManifestError",
    "ScreeningSignal",
    "ScreeningSupport",
    "ShiftPredictorEvalError",
    "load_screening_manifest",
    "run_screening_evaluation",
    "write_screening_evaluation",
]
