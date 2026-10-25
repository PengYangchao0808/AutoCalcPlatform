"""Ensemble thermochemistry bound to the ACP2 science protocol (A-24b).

The existing ensemble science lives in :mod:`cccp.analysis.ensemble.thermo`
(mixing entropy, T·S_mix and the rank-1 total-Gibbs formula) and in
:mod:`cccp.qc.thermo_normalize` (the standard-state normalization).  This
module is the *typing + weight policy + evidence* seam around those existing
functions; it does not add a second thermochemistry engine.

Weight policy
-------------
Exactly two supplied forms are accepted, and the supplied form is recorded:

``raw``
    Finite, strictly positive component weights with a non-zero sum.  They are
    normalized by that sum; the supplied sum and the normalization factor are
    recorded together with the declared tolerance.

``normalized``
    An explicitly normalized set whose sum is within the declared absolute
    tolerance of 1.  Values outside that tolerance are rejected, never
    silently renormalized.

Every other form is rejected: negative, zero, non-finite, empty or
non-numeric weights, an unknown mode, and a non-positive tolerance all raise
:class:`WeightPolicyError`.  There is no "normalize anything" fallback.

A missing component (no weight or no Gibbs value) is reported as
``components_missing`` and the outcome is ``incomplete`` with a ``total_gibbs``
of ``None``: a partial ensemble total is never presented as the ensemble total.

The module is pure analysis: standard library plus the existing ensemble/
thermo-normalize functions and the research evidence/provenance types.  It does
not import a task or QC execution module and never starts a subprocess.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from cccp.analysis.ensemble.thermo import (
    ensemble_total_gibbs,
    s_mix_kcal_per_mol_kelvin,
    t_s_mix_kcal_per_mol,
)
from cccp.analysis.research._common import canonical_json
from cccp.analysis.research.evidence import (
    ArtifactSourceRef,
    ClaimDraft,
    ClaimScope,
    Digest,
    EvidenceMissingCheck,
    EvidenceReport,
    EvidenceValidator,
    ExecutionFacts,
    Limitation,
    Subject,
    assemble_evidence_report,
)
from cccp.analysis.research.provenance import (
    Conditions,
    InputIdentity,
    ProvenanceArtifactRef,
    PureAnalysisDerivation,
    ScientificProvenance,
    SoftwareIdentity,
)
from cccp.analysis.research.validators import VALIDATOR_VERSION
from cccp.qc.thermo_normalize import normalize_standard_state
from cccp.utils.constants import HARTREE_TO_KCAL

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_WEIGHT_TOLERANCE",
    "ENSEMBLE_WEIGHT_POLICY_VERSION",
    "ENSEMBLE_THERMO_MODULE",
    "ENSEMBLE_THERMO_SCHEMA",
    "ENSEMBLE_THERMO_SCHEMA_VERSION",
    "ENSEMBLE_THERMO_VERSION",
    "ENSEMBLE_CONDITIONS_VALIDATOR_ID",
    "ENSEMBLE_COMPLETENESS_VALIDATOR_ID",
    "ENSEMBLE_TOTAL_VALIDATOR_ID",
    "ENSEMBLE_WEIGHT_VALIDATOR_ID",
    "WEIGHT_MODE_NORMALIZED",
    "WEIGHT_MODE_RAW",
    "WEIGHT_MODES",
    "EnsembleComponent",
    "EnsembleThermochemistryInput",
    "EnsembleThermochemistryOutcome",
    "NormalizedWeights",
    "WeightPolicyError",
    "build_ensemble_thermochemistry",
    "normalize_ensemble_weights",
]

ENSEMBLE_THERMO_MODULE: Final = "cccp.analysis.ensemble.science"
ENSEMBLE_THERMO_SCHEMA: Final = "acp2.science.ensemble-thermochemistry"
ENSEMBLE_THERMO_SCHEMA_VERSION: Final = 1
ENSEMBLE_THERMO_VERSION: Final = "1"
ENSEMBLE_WEIGHT_POLICY_VERSION: Final = "1"

WEIGHT_MODE_RAW: Final = "raw"
WEIGHT_MODE_NORMALIZED: Final = "normalized"
WEIGHT_MODES: Final[tuple[str, ...]] = (WEIGHT_MODE_RAW, WEIGHT_MODE_NORMALIZED)

#: Absolute tolerance on the supplied sum for ``weights_mode="normalized"``.
DEFAULT_WEIGHT_TOLERANCE: Final = 1e-9

ENSEMBLE_WEIGHT_VALIDATOR_ID: Final = "check.ensemble_weight_policy"
ENSEMBLE_COMPLETENESS_VALIDATOR_ID: Final = "check.ensemble_completeness"
ENSEMBLE_CONDITIONS_VALIDATOR_ID: Final = "check.ensemble_conditions"
ENSEMBLE_TOTAL_VALIDATOR_ID: Final = "check.ensemble_total_gibbs"

_ROLE_COMPONENT_TABLE: Final = "component_energy_table"


class WeightPolicyError(ValueError):
    """A supplied weight set violates the explicit ensemble weight policy."""

    def __init__(
        self,
        reason: str,
        *,
        mode: str | None = None,
        supplied_sum: float | None = None,
        tolerance: float | None = None,
    ) -> None:
        """Create the typed policy error with the offending values."""
        super().__init__(reason)
        self.reason = reason
        self.mode = mode
        self.supplied_sum = supplied_sum
        self.tolerance = tolerance


@dataclass(frozen=True, slots=True)
class NormalizedWeights:
    """The recorded result of one weight-policy application."""

    mode_supplied: str
    tolerance: float
    supplied_sum: float
    normalization: str
    normalization_factor: float
    normalized: tuple[float, ...]

    @property
    def normalized_sum(self) -> float:
        """Return the sum of the normalized weights."""
        return math.fsum(self.normalized)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe weight-policy record."""
        return {
            "policy_version": ENSEMBLE_WEIGHT_POLICY_VERSION,
            "mode_supplied": self.mode_supplied,
            "tolerance": self.tolerance,
            "supplied_sum": self.supplied_sum,
            "normalization": self.normalization,
            "normalization_factor": self.normalization_factor,
            "normalized": list(self.normalized),
            "normalized_sum": self.normalized_sum,
            "count": len(self.normalized),
        }


def _finite_positive(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise WeightPolicyError(f"{field} must be a real number, not a boolean")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise WeightPolicyError(f"{field} is not a number") from exc
    if not math.isfinite(number):
        raise WeightPolicyError(f"{field} must be finite")
    if number <= 0.0:
        raise WeightPolicyError(f"{field} must be strictly positive")
    return number


def normalize_ensemble_weights(
    weights: Sequence[float],
    *,
    mode: str,
    tolerance: float = DEFAULT_WEIGHT_TOLERANCE,
) -> NormalizedWeights:
    """Validate and normalize one component-weight set under the frozen policy.

    Args:
        weights: Finite, strictly positive component weights.
        mode: ``"raw"`` or ``"normalized"``; the supplied form is recorded.
        tolerance: Absolute tolerance for the normalized-sum check.

    Returns:
        The normalized weights plus the recorded policy/factor.

    Raises:
        WeightPolicyError: Empty, negative, zero, non-finite or non-numeric
            weights; an unknown mode; a non-positive tolerance; or an
            explicitly normalized set outside the declared tolerance.
    """
    if mode not in WEIGHT_MODES:
        raise WeightPolicyError(
            f"weights_mode must be one of {WEIGHT_MODES}, got {mode!r}",
            mode=mode,
        )
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise WeightPolicyError("weight tolerance must be a real number")
    tolerance_value = float(tolerance)
    if not math.isfinite(tolerance_value) or tolerance_value <= 0.0:
        raise WeightPolicyError("weight tolerance must be positive and finite")
    entries = tuple(weights)
    if not entries:
        raise WeightPolicyError("at least one component weight is required", mode=mode)
    values = tuple(
        _finite_positive(value, field=f"weights[{index}]") for index, value in enumerate(entries)
    )
    supplied_sum = math.fsum(values)
    if not math.isfinite(supplied_sum) or supplied_sum <= 0.0:
        raise WeightPolicyError(
            f"component weight sum must be positive and finite, got {supplied_sum!r}",
            mode=mode,
            supplied_sum=supplied_sum,
            tolerance=tolerance_value,
        )
    if mode == WEIGHT_MODE_NORMALIZED:
        if abs(supplied_sum - 1.0) > tolerance_value:
            raise WeightPolicyError(
                "weights_mode='normalized' requires a supplied sum within "
                f"{tolerance_value!r} of 1.0, got {supplied_sum!r}",
                mode=mode,
                supplied_sum=supplied_sum,
                tolerance=tolerance_value,
            )
        normalization = "accepted_explicitly_normalized_within_tolerance"
    else:
        normalization = "renormalized_from_raw_weights"
    factor = 1.0 / supplied_sum
    normalized = tuple(value * factor for value in values)
    if any(not math.isfinite(value) or value <= 0.0 for value in normalized):
        raise WeightPolicyError(
            "normalized weights must be finite and strictly positive",
            mode=mode,
            supplied_sum=supplied_sum,
            tolerance=tolerance_value,
        )
    return NormalizedWeights(
        mode_supplied=mode,
        tolerance=tolerance_value,
        supplied_sum=supplied_sum,
        normalization=normalization,
        normalization_factor=factor,
        normalized=normalized,
    )


@dataclass(frozen=True, slots=True)
class EnsembleComponent:
    """One ensemble component and its supplied Gibbs value/weight.

    ``gibbs_hartree`` and ``weight`` are nullable so a missing component can be
    reported as incomplete instead of being silently dropped.
    """

    component_id: str
    gibbs_hartree: float | None = None
    weight: float | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not str(self.component_id).strip():
            raise WeightPolicyError("component_id must be non-blank")
        object.__setattr__(self, "component_id", str(self.component_id))
        for name in ("gibbs_hartree", "weight"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool):
                raise WeightPolicyError(f"{self.component_id}.{name} must be a real number")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise WeightPolicyError(f"{self.component_id}.{name} is not a number") from exc
            if not math.isfinite(number):
                raise WeightPolicyError(f"{self.component_id}.{name} must be finite")
            object.__setattr__(self, name, number)
        if self.source is not None:
            object.__setattr__(self, "source", str(self.source))

    @property
    def complete(self) -> bool:
        """Whether the component carries both a Gibbs value and a weight."""
        return self.gibbs_hartree is not None and self.weight is not None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe component record."""
        return {
            "component_id": self.component_id,
            "gibbs_hartree": self.gibbs_hartree,
            "weight": self.weight,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class EnsembleThermochemistryInput:
    """One ensemble-thermochemistry request over supplied component records."""

    ensemble_id: str
    components: tuple[EnsembleComponent, ...]
    temperature_kelvin: float
    weights_mode: str
    source_artifacts: tuple[ProvenanceArtifactRef, ...]
    standard_state: str | None = "1atm"
    weight_tolerance: float = DEFAULT_WEIGHT_TOLERANCE

    def __post_init__(self) -> None:
        if not str(self.ensemble_id).strip():
            raise WeightPolicyError("ensemble_id must be non-blank")
        components = tuple(self.components)
        if not components:
            raise WeightPolicyError("at least one ensemble component is required")
        for component in components:
            if not isinstance(component, EnsembleComponent):
                raise WeightPolicyError("components must be EnsembleComponent records")
        ids = [component.component_id for component in components]
        if len(ids) != len(set(ids)):
            raise WeightPolicyError("component_ids must be unique")
        object.__setattr__(self, "components", components)
        temperature = _finite_positive(self.temperature_kelvin, field="temperature_kelvin")
        object.__setattr__(self, "temperature_kelvin", temperature)
        if self.weights_mode not in WEIGHT_MODES:
            raise WeightPolicyError(
                f"weights_mode must be one of {WEIGHT_MODES}, got {self.weights_mode!r}",
                mode=self.weights_mode,
            )
        if isinstance(self.weight_tolerance, bool) or not isinstance(
            self.weight_tolerance, (int, float)
        ):
            raise WeightPolicyError("weight_tolerance must be a real number")
        tolerance = float(self.weight_tolerance)
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            raise WeightPolicyError("weight_tolerance must be positive and finite")
        object.__setattr__(self, "weight_tolerance", tolerance)
        if self.standard_state is not None:
            object.__setattr__(self, "standard_state", str(self.standard_state))
        artifacts = tuple(self.source_artifacts)
        if not artifacts:
            raise WeightPolicyError(
                "at least one digested source artifact is required: an unrecorded "
                "ensemble cannot produce evidence"
            )
        for artifact in artifacts:
            if not isinstance(artifact, ProvenanceArtifactRef):
                raise WeightPolicyError("source_artifacts entries must be ProvenanceArtifactRef")
        object.__setattr__(self, "source_artifacts", artifacts)

    def missing_components(self) -> tuple[str, ...]:
        """Return component ids without both a weight and a Gibbs value."""
        return tuple(
            component.component_id for component in self.components if not component.complete
        )

    def normalized_standard_state(self) -> str:
        """Return the existing thermochemistry normalization of the supplied state."""
        return normalize_standard_state(self.standard_state)

    def standard_state_declared(self) -> bool:
        """Whether a non-blank standard state was supplied."""
        return bool(self.standard_state and self.standard_state.strip())

    def preflight_checks(self) -> tuple[dict[str, object], ...]:
        """Return side-effect-free scientific preconditions for the binding."""
        missing = self.missing_components()
        checks: list[dict[str, object]] = [
            {
                "id": "components_declared",
                "satisfied": len(self.components) > 0,
                "reason": f"{len(self.components)} component(s) declared",
                "expected": ">= 1 component",
                "actual": len(self.components),
            },
            {
                "id": "temperature_declared",
                "satisfied": math.isfinite(self.temperature_kelvin)
                and self.temperature_kelvin > 0.0,
                "reason": "a positive finite ensemble temperature was supplied",
                "expected": "> 0 K",
                "actual": self.temperature_kelvin,
            },
            {
                "id": "standard_state_declared",
                "satisfied": self.standard_state_declared(),
                "reason": (
                    "a standard state was supplied"
                    if self.standard_state_declared()
                    else "no standard state was supplied; the result stays incomplete"
                ),
                "expected": "1atm | 1M",
                "actual": self.standard_state,
            },
            {
                "id": "component_weights_declared",
                "satisfied": not missing,
                "reason": (
                    "every component carries a weight"
                    if not missing
                    else f"missing weight/Gibbs for {list(missing)}"
                ),
                "expected": "one finite positive weight per component",
                "actual": {"missing": list(missing)},
            },
        ]
        weights = [
            component.weight for component in self.components if component.weight is not None
        ]
        try:
            record = normalize_ensemble_weights(
                weights, mode=self.weights_mode, tolerance=self.weight_tolerance
            )
            policy_ok: bool | None = True
            policy_reason = (
                f"weights_mode={self.weights_mode!r} accepted; supplied sum={record.supplied_sum!r}"
            )
            policy_actual: object = record.to_dict()
        except WeightPolicyError as error:
            policy_ok = False
            policy_reason = error.reason
            policy_actual = {"error": error.reason}
        checks.append(
            {
                "id": "weights_policy_valid",
                "satisfied": policy_ok,
                "reason": policy_reason,
                "expected": (
                    f"finite positive weights; mode={self.weights_mode!r}; "
                    f"tolerance={self.weight_tolerance!r}"
                ),
                "actual": policy_actual,
            }
        )
        return tuple(checks)


@dataclass(frozen=True, slots=True)
class EnsembleThermochemistryOutcome:
    """One ensemble-thermochemistry result with provenance and evidence."""

    status: str
    ensemble_id: str
    temperature_kelvin: float
    standard_state_supplied: str | None
    standard_state_normalized: str
    weights: NormalizedWeights | None
    components_used: tuple[str, ...]
    components_missing: tuple[str, ...]
    rank1_component_id: str | None
    rank1_weight: float | None
    total_gibbs_hartree: float | None
    total_gibbs_kcal_mol: float | None
    mixing_entropy_kcal_per_mol_kelvin: float | None
    t_s_mix_kcal_per_mol: float | None
    provenance: ScientificProvenance
    evidence: EvidenceReport
    limitations: tuple[str, ...] = ()
    schema: str = ENSEMBLE_THERMO_SCHEMA
    schema_version: int = ENSEMBLE_THERMO_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe ensemble result record."""
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "status": self.status,
            "ensemble_id": self.ensemble_id,
            "temperature_kelvin": self.temperature_kelvin,
            "standard_state_supplied": self.standard_state_supplied,
            "standard_state_normalized": self.standard_state_normalized,
            "weights": None if self.weights is None else self.weights.to_dict(),
            "components_used": list(self.components_used),
            "components_missing": list(self.components_missing),
            "rank1_component_id": self.rank1_component_id,
            "rank1_weight": self.rank1_weight,
            "total_gibbs_hartree": self.total_gibbs_hartree,
            "total_gibbs_kcal_mol": self.total_gibbs_kcal_mol,
            "mixing_entropy_kcal_per_mol_kelvin": (self.mixing_entropy_kcal_per_mol_kelvin),
            "t_s_mix_kcal_per_mol": self.t_s_mix_kcal_per_mol,
            "limitations": list(self.limitations),
            "provenance": self.provenance.to_dict(),
            "evidence": self.evidence.to_dict(),
        }


def build_ensemble_thermochemistry(
    payload: EnsembleThermochemistryInput,
) -> EnsembleThermochemistryOutcome:
    """Build the ensemble total-Gibbs result, provenance and evidence report.

    Args:
        payload: The typed request (components, temperature, standard state,
            weights mode/tolerance and digested source artifacts).

    Returns:
        The typed outcome.  A missing component makes the result ``incomplete``
        with no total; an unspecified standard state or a partial claim makes
        it ``inconclusive``; only a fully verified result is ``supported``.

    Raises:
        WeightPolicyError: The supplied weights or component data violate the
            explicit policy (negative/zero/non-finite weights, an explicitly
            normalized set outside tolerance, duplicate ids, missing inputs).
    """
    if not isinstance(payload, EnsembleThermochemistryInput):
        raise WeightPolicyError("build_ensemble_thermochemistry requires an ensemble input")
    missing = payload.missing_components()
    present_weights = [
        component.weight for component in payload.components if component.weight is not None
    ]
    policy_record = (
        normalize_ensemble_weights(
            present_weights,  # type: ignore[arg-type]
            mode=payload.weights_mode,
            tolerance=payload.weight_tolerance,
        )
        if present_weights
        else None
    )
    weights: NormalizedWeights | None = None
    total: float | None = None
    total_kcal: float | None = None
    s_mix_kcal: float | None = None
    t_s_mix: float | None = None
    rank1_id: str | None = None
    rank1_weight: float | None = None
    components_used: tuple[str, ...] = ()
    normalized_state = payload.normalized_standard_state()

    if not missing:
        weights = policy_record
        ordered = sorted(
            payload.components,
            key=lambda component: (component.gibbs_hartree, component.component_id),  # type: ignore[arg-type,return-value]
        )
        weight_by_id = {
            component.component_id: weight
            for component, weight in zip(payload.components, weights.normalized)
        }
        rank1 = ordered[0]
        rank1_id = rank1.component_id
        rank1_weight = weight_by_id[rank1.component_id]
        total = ensemble_total_gibbs(
            float(rank1.gibbs_hartree), rank1_weight, payload.temperature_kelvin
        )
        total_kcal = total * HARTREE_TO_KCAL
        s_mix_kcal = s_mix_kcal_per_mol_kelvin(list(weights.normalized))
        t_s_mix = t_s_mix_kcal_per_mol(list(weights.normalized), payload.temperature_kelvin)
        components_used = tuple(component.component_id for component in payload.components)

    limitation_records: list[Limitation] = [
        Limitation(
            limitation_id="ensemble_scope",
            description=(
                "This report applies the supplied component weights and the existing "
                "rank-1 total-Gibbs formula. It does not re-derive Boltzmann weights, "
                "re-optimize conformers or claim a new thermochemistry calculation."
            ),
        )
    ]
    if missing:
        limitation_records.append(
            Limitation(
                limitation_id="ensemble_incomplete",
                description=(
                    "At least one component has no weight or no Gibbs value; no ensemble "
                    "total is reported and the missing components are listed explicitly."
                ),
            )
        )
    if not payload.standard_state_declared():
        limitation_records.append(
            Limitation(
                limitation_id="standard_state_unspecified",
                description=(
                    "No standard state was supplied; the existing thermochemistry "
                    "normalization defaults to 1atm, but no standard-state claim is made."
                ),
            )
        )
    limitation_ids = tuple(record.limitation_id for record in limitation_records)

    payload_document = {
        "ensemble_id": payload.ensemble_id,
        "components": [component.to_dict() for component in payload.components],
        "temperature_kelvin": payload.temperature_kelvin,
        "standard_state": payload.standard_state,
        "weights_mode": payload.weights_mode,
        "weight_tolerance": payload.weight_tolerance,
        "source_artifacts": [artifact.to_dict() for artifact in payload.source_artifacts],
    }
    input_digest = Digest.from_bytes(canonical_json(payload_document).encode("utf-8"))
    parameters: dict[str, object] = {
        "ensemble_id": payload.ensemble_id,
        "temperature_kelvin": payload.temperature_kelvin,
        "standard_state_supplied": payload.standard_state,
        "standard_state_normalized": normalized_state,
        "weight_policy_version": ENSEMBLE_WEIGHT_POLICY_VERSION,
        "weight_mode_supplied": payload.weights_mode,
        "weight_tolerance": payload.weight_tolerance,
        "component_ids": [component.component_id for component in payload.components],
        "components_used": list(components_used),
        "components_missing": list(missing),
        "rank1_component_id": rank1_id,
        "energy_unit": "hartree",
        "derived_energy_unit": "kcal_per_mol",
    }
    if weights is not None:
        parameters.update(
            {
                "weights_supplied": [component.weight for component in payload.components],
                "weights_normalized": list(weights.normalized),
                "weights_supplied_sum": weights.supplied_sum,
                "weights_normalized_sum": weights.normalized_sum,
                "weights_normalization": weights.normalization,
                "weights_normalization_factor": weights.normalization_factor,
                "rank1_weight": rank1_weight,
                "total_gibbs_hartree": total,
                "total_gibbs_kcal_mol": total_kcal,
                "s_mix_kcal_per_mol_kelvin": s_mix_kcal,
                "t_s_mix_kcal_per_mol": t_s_mix,
            }
        )
    provenance = ScientificProvenance(
        provenance_id=f"ensemble-thermo:{payload.ensemble_id}",
        result_kind="pure_analysis",
        software=SoftwareIdentity(
            software_id=ENSEMBLE_THERMO_MODULE,
            software_version=ENSEMBLE_THERMO_VERSION,
            distribution="cccp",
        ),
        derivation=PureAnalysisDerivation(
            algorithm="ensemble_thermochemistry",
            algorithm_version=ENSEMBLE_THERMO_VERSION,
            input_digest=input_digest,
            parameters=parameters,
        ),
        input=InputIdentity(artifacts=payload.source_artifacts),
        conditions=Conditions(
            temperature_kelvin=payload.temperature_kelvin,
            standard_state=normalized_state,
            reference_state=None,
            pressure_pa=None,
            phase=None,
            solvent=None,
        ),
        limitations=limitation_ids,
    )

    source_refs = tuple(
        ArtifactSourceRef(
            artifact_path=artifact.relative_path,
            digest=artifact.digest,
            role=artifact.role,
            units=artifact.units,
        )
        for artifact in payload.source_artifacts
        if artifact.role == _ROLE_COMPONENT_TABLE
    )
    weights_ok = weights is not None
    conditions_ok = payload.standard_state_declared()
    total_ok = total is not None and math.isfinite(total)
    drafts: list[ClaimDraft] = [
        ClaimDraft(
            claim_id="thermochemistry.weight_policy",
            statement=(
                "The supplied component weights satisfy the explicit policy; the "
                "supplied form, tolerance, sum and resulting normalized weights are "
                "recorded."
            ),
            status="supported" if weights_ok else "inconclusive",
            scope=ClaimScope(
                subject=f"{payload.ensemble_id}#weights",
                applicability="ensemble_specific",
                conditions=(f"mode={payload.weights_mode}; tolerance={payload.weight_tolerance}"),
            ),
            source_refs=source_refs,
            missing_checks=(
                ()
                if weights_ok
                else (
                    EvidenceMissingCheck(
                        check_id="partial_output",
                        reason="not every component carries a weight and a Gibbs value",
                    ),
                )
            ),
            validator=EvidenceValidator(
                ENSEMBLE_WEIGHT_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=("ensemble_scope",),
        ),
        ClaimDraft(
            claim_id="thermochemistry.ensemble_completeness",
            statement=(
                "Every declared ensemble component contributes a weight and a Gibbs "
                "value; no component is silently dropped."
            ),
            status="supported" if not missing else "inconclusive",
            scope=ClaimScope(
                subject=f"{payload.ensemble_id}#completeness",
                applicability="ensemble_specific",
                conditions=f"components={len(payload.components)}",
            ),
            source_refs=source_refs,
            missing_checks=(
                ()
                if not missing
                else (
                    EvidenceMissingCheck(
                        check_id="partial_output",
                        reason=f"missing weight/Gibbs for components: {', '.join(missing)}",
                    ),
                )
            ),
            validator=EvidenceValidator(
                ENSEMBLE_COMPLETENESS_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=("ensemble_incomplete",) if missing else ("ensemble_scope",),
        ),
        ClaimDraft(
            claim_id="thermochemistry.conditions",
            statement=(
                "The ensemble temperature and standard state in force are recorded in provenance."
            ),
            status="supported" if conditions_ok else "not_evaluated",
            scope=ClaimScope(
                subject=f"{payload.ensemble_id}#conditions",
                applicability="condition_specific",
                conditions=(f"T={payload.temperature_kelvin} K; standard_state={normalized_state}"),
            ),
            source_refs=source_refs,
            missing_checks=(
                ()
                if conditions_ok
                else (
                    EvidenceMissingCheck(
                        check_id="standard_state_unspecified",
                        reason="no standard state was supplied for the ensemble",
                    ),
                )
            ),
            validator=EvidenceValidator(
                ENSEMBLE_CONDITIONS_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=(
                ("standard_state_unspecified",) if not conditions_ok else ("ensemble_scope",)
            ),
        ),
        ClaimDraft(
            claim_id="thermochemistry.total_gibbs",
            statement=(
                "The ensemble total Gibbs energy is computed from the recorded rank-1 "
                "component, its normalized weight and the supplied temperature."
            ),
            status="supported" if total_ok else "inconclusive",
            scope=ClaimScope(
                subject=f"{payload.ensemble_id}#total-gibbs",
                applicability="ensemble_specific",
                conditions=f"T={payload.temperature_kelvin} K; unit=hartree",
            ),
            source_refs=source_refs,
            missing_checks=(
                ()
                if total_ok
                else (
                    EvidenceMissingCheck(
                        check_id="partial_output",
                        reason=("the ensemble is incomplete, so no total Gibbs value is reported"),
                    ),
                )
            ),
            validator=EvidenceValidator(
                ENSEMBLE_TOTAL_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=("ensemble_incomplete",) if not total_ok else ("ensemble_scope",),
        ),
    ]
    evidence = assemble_evidence_report(
        report_id=f"ensemble-thermo:{payload.ensemble_id}",
        subject=Subject(
            subject_id=payload.ensemble_id,
            subject_kind="artifact_set",
            description=(
                "Ensemble thermochemistry over supplied component Gibbs values and weights"
            ),
            input_digests=tuple(artifact.digest for artifact in payload.source_artifacts),
        ),
        execution=ExecutionFacts(
            terminal_state="completed",
            argv=(f"{ENSEMBLE_THERMO_MODULE}.build_ensemble_thermochemistry",),
            exit_code=None,
        ),
        drafts=drafts,
        limitations=tuple(limitation_records),
        provenance_digest=provenance.content_digest(),
    )

    if missing:
        status = "incomplete"
    elif not conditions_ok or total is None:
        status = "inconclusive"
    elif all(claim.status == "supported" for claim in evidence.claims):
        status = "supported"
    else:
        status = "inconclusive"

    return EnsembleThermochemistryOutcome(
        status=status,
        ensemble_id=payload.ensemble_id,
        temperature_kelvin=payload.temperature_kelvin,
        standard_state_supplied=payload.standard_state,
        standard_state_normalized=normalized_state,
        weights=weights,
        components_used=components_used,
        components_missing=missing,
        rank1_component_id=rank1_id,
        rank1_weight=rank1_weight,
        total_gibbs_hartree=total,
        total_gibbs_kcal_mol=total_kcal,
        mixing_entropy_kcal_per_mol_kelvin=s_mix_kcal,
        t_s_mix_kcal_per_mol=t_s_mix,
        provenance=provenance,
        evidence=evidence,
        limitations=limitation_ids,
    )
