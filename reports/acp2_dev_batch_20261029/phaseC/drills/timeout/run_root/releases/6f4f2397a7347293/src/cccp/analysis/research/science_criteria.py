"""Frozen ADR-008 science criteria for the limited Diels-Alder slice (M1-05).

This module is the machine-readable twin of
``docs/reference/next-change/adr/ADR-008-limited-diels-alder-criteria.md``: one
row per scientific criterion, one row per admissible question and one row per
missing-evidence downgrade rule.  A test parses the ADR tables and asserts they
agree row for row, so the prose a reviewer signs and the table a run is
evaluated against cannot drift.

Three separations are structural, not advisory:

* **Criteria are frozen before any real calculation.**  Every numeric threshold
  is the validator-owned constant in :mod:`cccp.analysis.research.validators`;
  the downgrade rules are table data and the whole definition carries a digest
  (:func:`criteria_definition_digest`) that the ADR records.  No value here is
  derived from a measured outcome — changing the criteria needs a new version
  and a new approval, never an inline edit after seeing a result.
* **Evaluation input is digest-bound evidence.**  :class:`CriterionEvidence`
  carries the producing validator identity plus a digest recomputed from its own
  evidence payload (:func:`digest_evidence`).  A record whose digest does not
  match, whose validator is outside the frozen set for that criterion, whose
  validator version drifted, or that carries a status-override key is rejected
  (:class:`ScienceCriteriaError`) instead of being read as a verdict.
* **``scenario_result`` never decides ``claim_status``.**  Execution completion
  (``PASS``/``FAIL``/``NOT_VERIFIED``) is carried verbatim and is not an input to
  any status computation.  A completed run without evidence is
  ``not_evaluated`` — the "execution completed but scientifically inconclusive"
  sample — and never becomes a success.

Missing evidence degrades honestly: an absent calculation is ``not_evaluated``,
a partial or ambiguous one is ``inconclusive``, and only an affirmative
contradiction is ``refuted`` — and only for the rows the ADR marks refutable.  A
single IRC direction, for example, can never refute a bidirectional path.

The module is pure: standard library plus sibling research types, no task/QC/
platform import, no subprocess, and importable without ``acp``.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, get_args

from cccp.analysis.research._common import (
    SchemaViolationError,
    ScienceContractError,
    canonical_json,
    reject_unknown_keys,
    require_mapping,
    require_sequence,
    require_string,
)
from cccp.analysis.research.evidence import DIGEST_PATTERN, MISSING_CHECK_IDS, Claim
from cccp.analysis.research.question import EVIDENCE_TYPES
from cccp.analysis.research.validators import (
    ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
    ATOM_COUNT_TOLERANCE,
    COORDINATE_ABS_TOLERANCE_ANGSTROM,
    ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID,
    ENERGY_COMPARABILITY_VALIDATOR_ID,
    FREQUENCY_EVIDENCE_VALIDATOR_ID,
    FREQUENCY_MEMBERSHIP_VALIDATOR_ID,
    IMAGINARY_MODE_THRESHOLD_CM1,
    IRC_ATOM_ORDER_STABILITY_VALIDATOR_ID,
    IRC_BIDIRECTIONAL_COMPLETION_VALIDATOR_ID,
    IRC_ENDPOINT_IDENTITY_VALIDATOR_ID,
    LOW_FREQUENCY_THRESHOLD_CM1,
    METHOD_CONSISTENCY_VALIDATOR_ID,
    REACTANT_STOICHIOMETRY_VALIDATOR_ID,
    TS_MODE_MAPPING_VALIDATOR_ID,
    TS_SADDLE_CANDIDATE_VALIDATOR_ID,
    VALIDATOR_VERSION,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLE_SCHEMA",
    "BUNDLE_SCHEMA_VERSION",
    "CRITERIA",
    "CRITERIA_DOCUMENT",
    "CRITERIA_REGIME",
    "CRITERIA_SCHEMA",
    "CRITERIA_STATUSES",
    "CRITERIA_VERSION",
    "DEGRADATION_RULES",
    "FROZEN_THRESHOLDS",
    "QUESTION_ROWS",
    "REASON_FOR_STATUS",
    "REJECTION_RULES",
    "SCENARIO_RESULTS",
    "CriterionEvaluation",
    "CriterionEvidence",
    "CriterionRow",
    "CriteriaEvaluation",
    "CriterionStatus",
    "DowngradeRule",
    "QuestionEvaluation",
    "QuestionRow",
    "RejectionRule",
    "ScenarioResult",
    "ScienceCriteriaError",
    "Threshold",
    "criterion_evidence_from_claim",
    "criterion_row",
    "criteria_definition_digest",
    "criteria_definition_payload",
    "criteria_rows",
    "digest_evidence",
    "evaluate_bundle",
    "evaluate_criteria",
    "evaluate_criterion",
    "question_row",
    "threshold_tokens",
]

# ── frozen identity of the criteria definition ─────────────────────────────

#: Contract identity of the criteria table itself.  A semantic change (a new
#: criterion, a different threshold direction, a different downgrade rule) is a
#: new version and a new ADR approval — not an inline edit.
CRITERIA_SCHEMA: Final = "acp2.science.da-criteria.v1"
CRITERIA_VERSION: Final = "1.0.0"

#: Which science slice the rows belong to (the only active candidate).
CRITERIA_REGIME: Final = "diels_alder.limited.v1"

#: Human-readable twin.  A test asserts the tables in this document agree with
#: :data:`CRITERIA` / :data:`QUESTION_ROWS` / :data:`DEGRADATION_RULES`.
CRITERIA_DOCUMENT: Final = "docs/reference/next-change/adr/ADR-008-limited-diels-alder-criteria.md"

#: Fixture bundle identity (the positive/negative/no-conclusion samples).
BUNDLE_SCHEMA: Final = "acp2.science.criteria-evaluation"
BUNDLE_SCHEMA_VERSION: Final = 1

CriterionStatus = Literal["supported", "refuted", "inconclusive", "not_evaluated"]
CRITERIA_STATUSES: Final[frozenset[str]] = frozenset(get_args(CriterionStatus))

#: Execution-completion vocabulary (acceptance harness).  It is **not** a claim
#: status: a ``PASS`` scenario result with no evidence is still ``not_evaluated``.
ScenarioResult = Literal["PASS", "FAIL", "NOT_VERIFIED"]
SCENARIO_RESULTS: Final[tuple[str, ...]] = get_args(ScenarioResult)

REASON_FOR_STATUS: Final[dict[str, str]] = {
    "supported": "affirmed",
    "refuted": "contradiction",
    "inconclusive": "missing_evidence",
    "not_evaluated": "not_checked",
}

#: Keys that would let a result payload override the verdict.  They are refused
#: inside an evidence payload: the verdict is derived from the frozen criteria,
#: never supplied by the thing being judged.
STATUS_OVERRIDE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "ok",
        "passed",
        "accepted",
        "criteria_passed",
        "claim_status",
        "refuted",
        "status",
        "supported",
        "verdict",
    }
)


class ScienceCriteriaError(ScienceContractError):
    """A criteria row, evidence record or bundle violates a frozen rule."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """Return the typed, JSON-ready error view."""
        return {"rule": self.rule, "message": self.message}


# ── frozen thresholds (validator-owned, never measured here) ───────────────


@dataclass(frozen=True, slots=True)
class Threshold:
    """One frozen numeric threshold and the symbol that owns its value."""

    name: str
    value: float
    unit: str
    owner: str
    constructed: bool = False

    def token(self) -> str:
        """Return the ADR cell form ``name=value unit``."""
        number = int(self.value) if float(self.value).is_integer() else float(self.value)
        return f"{self.name}={number} {self.unit}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "owner": self.owner,
            "constructed": self.constructed,
        }


#: The four validator-owned thresholds the criteria rows cite.  ``owner`` names
#: the single source of the value so a row can never invent its own number.
FROZEN_THRESHOLDS: Final[dict[str, Threshold]] = {
    row.name: row
    for row in (
        Threshold(
            name="imaginary_mode_threshold_cm1",
            value=float(IMAGINARY_MODE_THRESHOLD_CM1),
            unit="cm-1",
            owner="validators.IMAGINARY_MODE_THRESHOLD_CM1",
        ),
        Threshold(
            name="low_frequency_threshold_cm1",
            value=float(LOW_FREQUENCY_THRESHOLD_CM1),
            unit="cm-1",
            owner="validators.LOW_FREQUENCY_THRESHOLD_CM1",
        ),
        Threshold(
            name="coordinate_abs_tolerance_angstrom",
            value=float(COORDINATE_ABS_TOLERANCE_ANGSTROM),
            unit="angstrom",
            owner="validators.COORDINATE_ABS_TOLERANCE_ANGSTROM",
        ),
        Threshold(
            name="atom_count_tolerance",
            value=float(ATOM_COUNT_TOLERANCE),
            unit="atoms",
            owner="validators.ATOM_COUNT_TOLERANCE",
        ),
    )
}


def threshold_tokens(names: Iterable[str]) -> tuple[str, ...]:
    """Return the ADR cells for *names* in the order given."""
    return tuple(FROZEN_THRESHOLDS[name].token() for name in names)


# ── downgrade and rejection rules ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DowngradeRule:
    """One frozen rule turning missing/partial evidence into a weaker status."""

    rule_id: str
    condition: str
    result: str
    never: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        for status in (self.result, *self.never):
            if status not in CRITERIA_STATUSES:
                raise ScienceCriteriaError(
                    "unknown_downgrade_status",
                    f"{self.rule_id}: {status!r} is not a claim status",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "condition": self.condition,
            "result": self.result,
            "never": list(self.never),
            "reason": self.reason,
        }


#: Every downgrade rule the ADR freezes.  A criterion row may cite one or more.
DEGRADATION_RULES: Final[tuple[DowngradeRule, ...]] = (
    DowngradeRule(
        rule_id="DG-01-evidence-absent",
        condition="no evidence record exists for the criterion",
        result="not_evaluated",
        never=("refuted",),
        reason="an absent calculation is missing evidence, not a contradiction",
    ),
    DowngradeRule(
        rule_id="DG-02-step-not-completed",
        condition="the producing step failed, was cancelled or stopped, or never dispatched",
        result="not_evaluated",
        never=("refuted",),
        reason="a failed step refutes that step's completion only, never the stronger proposition",
    ),
    DowngradeRule(
        rule_id="DG-03-partial-evidence",
        condition="a blocking missing check or partial output is declared with the verdict",
        result="inconclusive",
        never=("supported",),
        reason="partial evidence can neither affirm nor deny the claim",
    ),
    DowngradeRule(
        rule_id="DG-04-source-not-digested",
        condition="a supported or refuted verdict carries no digested source",
        result="inconclusive",
        never=("supported", "refuted"),
        reason="an unattributed verdict is not admissible evidence",
    ),
    DowngradeRule(
        rule_id="DG-05-single-irc-direction",
        condition="only one IRC direction reached a terminal state",
        result="inconclusive",
        never=("supported",),
        reason="one IRC direction never proves a bidirectional path",
    ),
)

_DOWNGRADE_BY_ID: Final[dict[str, DowngradeRule]] = {row.rule_id: row for row in DEGRADATION_RULES}


@dataclass(frozen=True, slots=True)
class RejectionRule:
    """One frozen rule that rejects an input instead of producing a status."""

    rule_id: str
    condition: str

    def to_dict(self) -> dict[str, str]:
        return {"rule_id": self.rule_id, "condition": self.condition}


#: Inputs refused with a typed error.  They are not claim statuses: a malformed
#: or foreign record is an error, never ``refuted``.
REJECTION_RULES: Final[tuple[RejectionRule, ...]] = (
    RejectionRule(
        rule_id="RJ-01-unknown-criterion",
        condition="the record or query names a criterion that is not frozen in this table",
    ),
    RejectionRule(
        rule_id="RJ-02-foreign-validator",
        condition="the record was produced by a validator outside the criterion's frozen set",
    ),
    RejectionRule(
        rule_id="RJ-03-validator-version-drift",
        condition="the record's validator version differs from the frozen validator version",
    ),
    RejectionRule(
        rule_id="RJ-04-evidence-not-digest-bound",
        condition="the evidence digest is malformed or does not match the evidence payload",
    ),
    RejectionRule(
        rule_id="RJ-05-status-override",
        condition="the evidence payload carries a status/verdict override key",
    ),
    RejectionRule(
        rule_id="RJ-06-unknown-field",
        condition="a closed record or bundle payload carries an undeclared field",
    ),
    RejectionRule(
        rule_id="RJ-07-duplicate-record",
        condition="two records claim the same criterion",
    ),
    RejectionRule(
        rule_id="RJ-08-refuted-not-allowed",
        condition="a record refutes a criterion the ADR marks as not refutable",
    ),
    RejectionRule(
        rule_id="RJ-09-unreasoned-status",
        condition="an inconclusive or not_evaluated record names no missing check",
    ),
    RejectionRule(
        rule_id="RJ-10-missing-field",
        condition="a closed record or bundle payload omits a required field",
    ),
    RejectionRule(
        rule_id="RJ-11-invalid-shape",
        condition="a closed record or bundle payload declares a field of the wrong type",
    ),
)

_REJECTION_IDS: Final[frozenset[str]] = frozenset(row.rule_id for row in REJECTION_RULES)

# ── criterion rows (the table the ADR freezes) ─────────────────────────────


@dataclass(frozen=True, slots=True)
class CriterionRow:
    """One frozen criterion: requirement, validators, thresholds and verdicts."""

    criterion_id: str
    area: str
    acceptance_row: str
    requirement: str
    required_evidence: tuple[str, ...]
    validator_ids: tuple[str, ...]
    threshold_names: tuple[str, ...]
    refutable: bool
    recipe_steps: tuple[str, ...]
    downgrade_rules: tuple[str, ...]
    supported: str
    refuted: str
    inconclusive: str
    not_evaluated: str

    def __post_init__(self) -> None:
        if not self.criterion_id.startswith("DA-C"):
            raise ScienceCriteriaError(
                "bad_criterion_id", f"{self.criterion_id!r} must start with 'DA-C'"
            )
        for evidence_type in self.required_evidence:
            if evidence_type not in EVIDENCE_TYPES:
                raise ScienceCriteriaError(
                    "unknown_evidence_type",
                    f"{self.criterion_id}: {evidence_type!r} is not a frozen evidence type",
                )
        for name in self.threshold_names:
            if name not in FROZEN_THRESHOLDS:
                raise ScienceCriteriaError(
                    "unknown_threshold", f"{self.criterion_id}: {name!r} is not frozen"
                )
        for rule_id in self.downgrade_rules:
            if rule_id not in _DOWNGRADE_BY_ID:
                raise ScienceCriteriaError(
                    "unknown_downgrade_rule", f"{self.criterion_id}: {rule_id!r} is not frozen"
                )

    @property
    def thresholds(self) -> tuple[Threshold, ...]:
        """The frozen thresholds this criterion cites, in table order."""
        return tuple(FROZEN_THRESHOLDS[name] for name in self.threshold_names)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "area": self.area,
            "acceptance_row": self.acceptance_row,
            "requirement": self.requirement,
            "required_evidence": list(self.required_evidence),
            "validator_ids": list(self.validator_ids),
            "thresholds": [row.to_dict() for row in self.thresholds],
            "refutable": self.refutable,
            "recipe_steps": list(self.recipe_steps),
            "downgrade_rules": list(self.downgrade_rules),
            "supported": self.supported,
            "refuted": self.refuted,
            "inconclusive": self.inconclusive,
            "not_evaluated": self.not_evaluated,
        }


#: The five required areas of ADR-008 — TS, frequency, bidirectional IRC,
#: endpoint association and energy comparability — expanded into seven rows
#: because a TS candidate and a mode mapping are different propositions and the
#: activation-energy subtraction needs a complete reactant set.
CRITERIA: Final[tuple[CriterionRow, ...]] = (
    CriterionRow(
        criterion_id="DA-C01-ts-single-imaginary-mode",
        area="ts",
        acceptance_row="DA-08-real-ts",
        requirement=(
            "The optimized transition-state candidate has exactly one imaginary frequency at "
            "the frozen threshold; this supports only a local first-order saddle candidate."
        ),
        required_evidence=("transition_state_geometry", "imaginary_frequency"),
        validator_ids=(TS_SADDLE_CANDIDATE_VALIDATOR_ID,),
        threshold_names=("imaginary_mode_threshold_cm1",),
        refutable=True,
        recipe_steps=("ts_frequency",),
        downgrade_rules=(
            "DG-01-evidence-absent",
            "DG-02-step-not-completed",
            "DG-03-partial-evidence",
        ),
        supported="exactly one mode lies below the imaginary threshold",
        refuted="the frequency list carries two or more imaginary modes, or none at all",
        inconclusive="frequencies exist but the list cannot be classified",
        not_evaluated="no frequency result exists for the optimized candidate",
    ),
    CriterionRow(
        criterion_id="DA-C02-ts-mode-mapping",
        area="ts",
        acceptance_row="DA-08-real-ts",
        requirement=(
            "The imaginary mode is identified by its native printed index and belongs to the "
            "optimized candidate within the frozen coordinate and atom-count tolerances."
        ),
        required_evidence=("transition_state_geometry", "imaginary_frequency"),
        validator_ids=(TS_MODE_MAPPING_VALIDATOR_ID, FREQUENCY_MEMBERSHIP_VALIDATOR_ID),
        threshold_names=("coordinate_abs_tolerance_angstrom", "atom_count_tolerance"),
        refutable=True,
        recipe_steps=("candidate_frequency", "ts_optimize", "ts_frequency"),
        downgrade_rules=(
            "DG-01-evidence-absent",
            "DG-02-step-not-completed",
            "DG-03-partial-evidence",
        ),
        supported=(
            "the mapped mode resolves to one native printed index and the compared "
            "geometries agree within tolerance"
        ),
        refuted=(
            "the mapped mode is not resolved, or the frequency geometry differs beyond "
            "tolerance, or the atom count differs"
        ),
        inconclusive="the frequency or optimization result lacks the mode vectors needed to map",
        not_evaluated="no mode selection or frequency result exists",
    ),
    CriterionRow(
        criterion_id="DA-C03-frequency-evidence",
        area="frequency",
        acceptance_row="DA-08-real-ts",
        requirement=(
            "The frequency result carries parsed Hessian normal-mode evidence for a complete "
            "real-mode list, and every real mode at or below the low-frequency threshold is "
            "reported as soft."
        ),
        required_evidence=("imaginary_frequency", "transition_state_geometry"),
        validator_ids=(FREQUENCY_EVIDENCE_VALIDATOR_ID,),
        threshold_names=("low_frequency_threshold_cm1", "imaginary_mode_threshold_cm1"),
        refutable=False,
        recipe_steps=("ts_frequency",),
        downgrade_rules=(
            "DG-01-evidence-absent",
            "DG-02-step-not-completed",
            "DG-03-partial-evidence",
        ),
        supported=(
            "every expected mode index has a normal-mode vector with the structure's atom count"
        ),
        refuted=(
            "never: a missing Hessian or mode vector is missing evidence, not a contradiction"
        ),
        inconclusive=(
            "the result carries no frequencies, no parsed analysis, or mode vectors are missing"
        ),
        not_evaluated="no frequency result exists",
    ),
    CriterionRow(
        criterion_id="DA-C04-irc-bidirectional",
        area="irc",
        acceptance_row="DA-09-real-irc",
        requirement=(
            "Both IRC directions from the optimized candidate completed with native steps and a "
            "final structure; one direction alone never proves a bidirectional path."
        ),
        required_evidence=("irc_forward", "irc_reverse"),
        validator_ids=(IRC_BIDIRECTIONAL_COMPLETION_VALIDATOR_ID,),
        threshold_names=(),
        refutable=False,
        recipe_steps=("irc",),
        downgrade_rules=(
            "DG-01-evidence-absent",
            "DG-02-step-not-completed",
            "DG-05-single-irc-direction",
        ),
        supported=(
            "forward and reverse both succeeded, converged and carry native steps and "
            "a final structure"
        ),
        refuted=(
            "never: a one-sided or non-converged direction is missing evidence, not a contradiction"
        ),
        inconclusive="a direction is absent, failed, unconverged or carries no final structure",
        not_evaluated="no IRC result exists",
    ),
    CriterionRow(
        criterion_id="DA-C05-endpoint-association",
        area="endpoint",
        acceptance_row="DA-09-real-irc",
        requirement=(
            "Each IRC direction reaches exactly one frozen endpoint under the declared atom "
            "mapping with a stable atom order; the direction label is never trusted."
        ),
        required_evidence=("path_endpoints", "irc_forward", "irc_reverse"),
        validator_ids=(IRC_ENDPOINT_IDENTITY_VALIDATOR_ID, IRC_ATOM_ORDER_STABILITY_VALIDATOR_ID),
        threshold_names=("atom_count_tolerance", "coordinate_abs_tolerance_angstrom"),
        refutable=True,
        recipe_steps=("endpoint_reactants", "endpoint_product"),
        downgrade_rules=(
            "DG-01-evidence-absent",
            "DG-02-step-not-completed",
            "DG-03-partial-evidence",
        ),
        supported="both directions resolve to the declared endpoints with a stable atom order",
        refuted=(
            "the final structure affirmatively reproduces neither frozen endpoint "
            "under the mapped bond topology"
        ),
        inconclusive="the endpoint mapping is ambiguous, unresolved or the atom order is unstable",
        not_evaluated="no endpoint identity or atom-order evidence exists",
    ),
    CriterionRow(
        criterion_id="DA-C06-energy-comparability",
        area="energy",
        acceptance_row="DA-10-real-energy",
        requirement=(
            "The compared energies share method, basis, electronic state, temperature, "
            "pressure, standard state, reference state, energy kind and explicit unit before "
            "any subtraction."
        ),
        required_evidence=("energy_difference", "thermochemistry"),
        validator_ids=(
            METHOD_CONSISTENCY_VALIDATOR_ID,
            ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID,
            ENERGY_COMPARABILITY_VALIDATOR_ID,
            ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
        ),
        threshold_names=(),
        refutable=True,
        recipe_steps=("endpoint_reactants", "endpoint_product", "energy_thermochemistry"),
        downgrade_rules=(
            "DG-01-evidence-absent",
            "DG-02-step-not-completed",
            "DG-03-partial-evidence",
        ),
        supported="every comparability identity matches on both sides and the unit is explicit",
        refuted=(
            "a declared method, electronic state or standard state differs, or the "
            "energy unit or kind is incompatible"
        ),
        inconclusive="an identity field or an energy value is missing on at least one side",
        not_evaluated="no energy value exists for a required participant",
    ),
    CriterionRow(
        criterion_id="DA-C07-reactant-stoichiometry",
        area="energy",
        acceptance_row="DA-10-real-energy",
        requirement=(
            "Every reactant component of the frozen reference state is present with its declared "
            "coefficient, so the activation-energy subtraction is defined."
        ),
        required_evidence=("energy_difference", "path_endpoints"),
        validator_ids=(REACTANT_STOICHIOMETRY_VALIDATOR_ID,),
        threshold_names=("atom_count_tolerance",),
        refutable=True,
        recipe_steps=("endpoint_reactants", "energy_thermochemistry"),
        downgrade_rules=("DG-01-evidence-absent", "DG-02-step-not-completed"),
        supported=(
            "the supplied reactant set equals the declared required set with matching coefficients"
        ),
        refuted=(
            "a required reactant is missing, an unexpected component is present, or a "
            "coefficient differs"
        ),
        inconclusive="a component is declared but its energy record is incomplete",
        not_evaluated="no required reactant set or no reactant energy record exists",
    ),
)

_ROW_BY_ID: Final[dict[str, CriterionRow]] = {row.criterion_id: row for row in CRITERIA}


def criterion_row(criterion_id: str) -> CriterionRow:
    """Return the frozen row for *criterion_id* or raise ``RJ-01``."""
    row = _ROW_BY_ID.get(str(criterion_id))
    if row is None:
        raise ScienceCriteriaError(
            "RJ-01-unknown-criterion",
            f"{criterion_id!r} is not a frozen ADR-008 criterion",
        )
    return row


def criteria_rows() -> list[dict[str, Any]]:
    """Return the criteria table as JSON-ready contract rows (stable order)."""
    return [row.to_dict() for row in CRITERIA]


# ── admissible questions (how criteria combine into a claim) ───────────────


@dataclass(frozen=True, slots=True)
class QuestionRow:
    """One admissible question and how criterion verdicts combine for it."""

    question_id: str
    title: str
    required_criteria: tuple[str, ...]
    refuting_criteria: tuple[str, ...]
    supported: str
    refuted: str
    inconclusive: str
    not_evaluated: str

    def __post_init__(self) -> None:
        for criterion_id in self.required_criteria:
            criterion_row(criterion_id)
        for criterion_id in self.refuting_criteria:
            if criterion_id not in self.required_criteria:
                raise ScienceCriteriaError(
                    "refuting_not_required",
                    f"{self.question_id}: {criterion_id!r} refutes but is not required",
                )
            if not _ROW_BY_ID[criterion_id].refutable:
                raise ScienceCriteriaError(
                    "refuting_not_refutable",
                    f"{self.question_id}: {criterion_id!r} is not a refutable criterion",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "required_criteria": list(self.required_criteria),
            "refuting_criteria": list(self.refuting_criteria),
            "supported": self.supported,
            "refuted": self.refuted,
            "inconclusive": self.inconclusive,
            "not_evaluated": self.not_evaluated,
        }


#: The only two questions the frozen input admits.  A criterion that is a
#: prerequisite but not ``refuting`` can block a question (making it
#: ``inconclusive``) but never refutes it: a TS candidate that is not a
#: first-order saddle refutes the saddle claim, not the existence of a path.
QUESTION_ROWS: Final[tuple[QuestionRow, ...]] = (
    QuestionRow(
        question_id="question.diels_alder.path_connectivity.v1",
        title="Does the declared candidate path connect butadiene + ethene to cyclohexene?",
        required_criteria=(
            "DA-C01-ts-single-imaginary-mode",
            "DA-C02-ts-mode-mapping",
            "DA-C03-frequency-evidence",
            "DA-C04-irc-bidirectional",
            "DA-C05-endpoint-association",
        ),
        refuting_criteria=("DA-C05-endpoint-association",),
        supported="every required criterion is supported",
        refuted=(
            "the endpoint-association criterion is refuted: the IRC reaches neither "
            "declared endpoint"
        ),
        inconclusive="some required criterion is refuted but not refuting, or evidence is partial",
        not_evaluated="no required criterion has anything but an unevaluated verdict",
    ),
    QuestionRow(
        question_id="question.diels_alder.activation_energy.v1",
        title="What is the activation energy under the frozen method and conditions?",
        required_criteria=(
            "DA-C01-ts-single-imaginary-mode",
            "DA-C02-ts-mode-mapping",
            "DA-C03-frequency-evidence",
            "DA-C06-energy-comparability",
            "DA-C07-reactant-stoichiometry",
        ),
        refuting_criteria=("DA-C06-energy-comparability", "DA-C07-reactant-stoichiometry"),
        supported="every required criterion is supported",
        refuted=(
            "energy comparability or reactant stoichiometry is refuted for the frozen "
            "conditions and reference state"
        ),
        inconclusive="some required criterion is refuted but not refuting, or evidence is partial",
        not_evaluated="no required criterion has anything but an unevaluated verdict",
    ),
)

_QUESTION_BY_ID: Final[dict[str, QuestionRow]] = {row.question_id: row for row in QUESTION_ROWS}


def question_row(question_id: str) -> QuestionRow:
    """Return the frozen question row for *question_id* or raise."""
    row = _QUESTION_BY_ID.get(str(question_id))
    if row is None:
        raise ScienceCriteriaError(
            "unknown_question", f"{question_id!r} is not an admissible ADR-008 question"
        )
    return row


# ── digest-bound evidence records ──────────────────────────────────────────


def digest_evidence(payload: Mapping[str, Any]) -> str:
    """Return the bare sha256 of the canonical bytes of an evidence payload."""
    encoded = canonical_json(dict(payload)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_RECORD_KEYS: Final[tuple[str, ...]] = (
    "criterion_id",
    "claim_id",
    "status",
    "validator_id",
    "validator_version",
    "evidence",
    "evidence_digest",
    "source_digests",
    "missing_check_ids",
)


#: Nested keys that would still smuggle a verdict.  A nested ``status`` is a
#: legitimate per-direction/-mode value (``converged``, ``resolved``), so the
#: full override vocabulary applies only at the top level of the payload.
_NESTED_OVERRIDE_KEYS: Final[frozenset[str]] = frozenset(
    {"claim_status", "criteria_passed", "supported", "verdict"}
)


def _reject_status_overrides(payload: Any, where: str, *, top: bool = True) -> None:
    if isinstance(payload, Mapping):
        forbidden = STATUS_OVERRIDE_KEYS if top else _NESTED_OVERRIDE_KEYS
        for key, value in payload.items():
            if str(key) in forbidden:
                raise ScienceCriteriaError(
                    "RJ-05-status-override",
                    f"{where}: key {key!r} would let the result override the frozen verdict",
                )
            _reject_status_overrides(value, f"{where}.{key}", top=False)
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, item in enumerate(payload):
            _reject_status_overrides(item, f"{where}[{index}]", top=False)


def _digest_tuple(values: Any, where: str) -> tuple[str, ...]:
    digests = []
    for index, value in enumerate(require_sequence(values, where)):
        text = require_string(value, f"{where}[{index}]")
        if DIGEST_PATTERN.fullmatch(text) is None:
            raise ScienceCriteriaError(
                "RJ-04-evidence-not-digest-bound",
                f"{where}[{index}] must be a bare 64-character sha256 hex digest",
            )
        digests.append(text)
    return tuple(digests)


@dataclass(frozen=True, slots=True)
class CriterionEvidence:
    """One digest-bound evidence record for exactly one criterion.

    The record is the *only* evaluation input.  It carries the producing
    validator (id + version), the verdict that validator returned, the digests
    of the artifacts it consumed and a digest recomputed from ``evidence`` — so
    a caller cannot pass a bare ``True``, a scenario result or a mutated payload.
    """

    criterion_id: str
    claim_id: str
    status: str
    validator_id: str
    validator_version: str
    evidence: Mapping[str, Any]
    evidence_digest: str
    source_digests: tuple[str, ...] = ()
    missing_check_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        criterion_row(self.criterion_id)
        if not str(self.claim_id).strip():
            raise ScienceCriteriaError("bad_claim_id", "claim_id must be non-empty")
        if self.status not in CRITERIA_STATUSES:
            raise ScienceCriteriaError(
                "unknown_claim_status",
                f"{self.status!r} is not in the frozen vocabulary {sorted(CRITERIA_STATUSES)}",
            )
        if not str(self.validator_id).strip():
            raise ScienceCriteriaError(
                "RJ-02-foreign-validator", f"{self.criterion_id}: a validator id is required"
            )
        if not str(self.validator_version).strip():
            raise ScienceCriteriaError(
                "RJ-03-validator-version-drift",
                f"{self.criterion_id}: a validator version is required",
            )
        payload = require_mapping(self.evidence, f"{self.criterion_id}.evidence")
        _reject_status_overrides(payload, f"{self.criterion_id}.evidence")
        if DIGEST_PATTERN.fullmatch(str(self.evidence_digest)) is None:
            raise ScienceCriteriaError(
                "RJ-04-evidence-not-digest-bound",
                f"{self.criterion_id}: evidence_digest must be a bare 64-character sha256",
            )
        recomputed = digest_evidence(payload)
        if recomputed != self.evidence_digest:
            raise ScienceCriteriaError(
                "RJ-04-evidence-not-digest-bound",
                f"{self.criterion_id}: evidence_digest does not match the evidence payload",
            )
        object.__setattr__(self, "evidence", payload)
        object.__setattr__(
            self,
            "source_digests",
            _digest_tuple(self.source_digests, f"{self.criterion_id}.source_digests"),
        )
        missing = tuple(
            require_string(item, f"{self.criterion_id}.missing_check_ids[]")
            for item in self.missing_check_ids
        )
        for check_id in missing:
            if check_id not in MISSING_CHECK_IDS:
                raise ScienceCriteriaError(
                    "unknown_missing_check",
                    f"{self.criterion_id}: {check_id!r} is not in the frozen "
                    "missing-check vocabulary",
                )
        object.__setattr__(self, "missing_check_ids", missing)
        if self.status in ("refuted", "supported") and any(self.missing_check_ids):
            logger.debug(
                "%s: a %s record names missing checks; evaluation will downgrade it",
                self.criterion_id,
                self.status,
            )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "criterion_evidence") -> CriterionEvidence:
        """Read one record; unknown fields and status overrides are rejected."""
        try:
            data = require_mapping(payload, where)
            reject_unknown_keys(data, _RECORD_KEYS, where)
        except SchemaViolationError as error:
            raise ScienceCriteriaError("RJ-06-unknown-field", f"{where}: {error}") from error
        missing_keys = sorted(
            {
                "criterion_id",
                "claim_id",
                "status",
                "validator_id",
                "validator_version",
                "evidence",
                "evidence_digest",
            }
            - set(data)
        )
        if missing_keys:
            raise ScienceCriteriaError(
                "RJ-10-missing-field", f"{where}: missing required field(s) {missing_keys}"
            )
        try:
            return cls(
                criterion_id=require_string(data["criterion_id"], f"{where}.criterion_id"),
                claim_id=require_string(data["claim_id"], f"{where}.claim_id"),
                status=require_string(data["status"], f"{where}.status"),
                validator_id=require_string(data["validator_id"], f"{where}.validator_id"),
                validator_version=require_string(
                    data["validator_version"], f"{where}.validator_version"
                ),
                evidence=require_mapping(data["evidence"], f"{where}.evidence"),
                evidence_digest=require_string(data["evidence_digest"], f"{where}.evidence_digest"),
                source_digests=_digest_tuple(
                    data.get("source_digests", ()), f"{where}.source_digests"
                ),
                missing_check_ids=tuple(
                    require_string(item, f"{where}.missing_check_ids[]")
                    for item in require_sequence(
                        data.get("missing_check_ids", ()), f"{where}.missing_check_ids"
                    )
                ),
            )
        except SchemaViolationError as error:
            raise ScienceCriteriaError("RJ-11-invalid-shape", f"{where}: {error}") from error

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe record (the evidence payload is verbatim)."""
        return {
            "criterion_id": self.criterion_id,
            "claim_id": self.claim_id,
            "status": self.status,
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "evidence": dict(self.evidence),
            "evidence_digest": self.evidence_digest,
            "source_digests": list(self.source_digests),
            "missing_check_ids": list(self.missing_check_ids),
        }


def criterion_evidence_from_claim(claim: Claim, criterion_id: str) -> CriterionEvidence:
    """Adapt one admitted evidence claim into one criteria record.

    This is the only bridge between the evidence layer and this table: the
    record reuses the claim's validator identity, verdict, digested sources and
    missing checks, and its ``evidence`` payload *is* the claim document, so the
    criteria digest binds the exact claim bytes.  A second evidence model is
    deliberately not introduced.
    """
    if claim.validator is None:
        raise ScienceCriteriaError(
            "evidence_not_attributable",
            f"claim {claim.claim_id!r} carries no validator identity",
        )
    evidence = {"claim": claim.to_dict()}
    return CriterionEvidence(
        criterion_id=criterion_id,
        claim_id=claim.claim_id,
        status=claim.status,
        validator_id=claim.validator.validator_id,
        validator_version=claim.validator.validator_version,
        evidence=evidence,
        evidence_digest=digest_evidence(evidence),
        source_digests=tuple(source.digest.value for source in claim.source_refs),
        missing_check_ids=tuple(check.check_id for check in claim.missing_checks),
    )


# ── evaluation results ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CriterionEvaluation:
    """One criterion verdict, with the reason and any downgrade that applied."""

    criterion_id: str
    claim_status: str
    reason: str
    evidence_status: str | None
    validator_id: str | None
    evidence_digest: str | None
    missing_check_ids: tuple[str, ...] = ()
    applied_rules: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.claim_status not in CRITERIA_STATUSES:
            raise ScienceCriteriaError(
                "unknown_claim_status", f"{self.claim_status!r} is not a claim status"
            )
        expected = REASON_FOR_STATUS[self.claim_status]
        if self.reason != expected:
            raise ScienceCriteriaError(
                "bad_reason", f"{self.claim_status!r} requires reason {expected!r}"
            )
        for rule_id in self.applied_rules:
            if rule_id not in _DOWNGRADE_BY_ID and rule_id not in _REJECTION_IDS:
                raise ScienceCriteriaError("unknown_downgrade_rule", f"{rule_id!r} is not frozen")

    @property
    def supported(self) -> bool:
        """Whether the criterion is affirmatively supported."""
        return self.claim_status == "supported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "claim_status": self.claim_status,
            "reason": self.reason,
            "evidence_status": self.evidence_status,
            "validator_id": self.validator_id,
            "evidence_digest": self.evidence_digest,
            "missing_check_ids": list(self.missing_check_ids),
            "applied_rules": list(self.applied_rules),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class QuestionEvaluation:
    """One admissible question's verdict and the criteria that block it."""

    question_id: str
    claim_status: str
    reason: str
    blocked_by: tuple[str, ...] = ()
    refuted_by: tuple[str, ...] = ()
    missing_check_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.claim_status not in CRITERIA_STATUSES:
            raise ScienceCriteriaError(
                "unknown_claim_status", f"{self.claim_status!r} is not a claim status"
            )

    @property
    def supported(self) -> bool:
        """Whether the question is affirmatively supported."""
        return self.claim_status == "supported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "claim_status": self.claim_status,
            "reason": self.reason,
            "blocked_by": list(self.blocked_by),
            "refuted_by": list(self.refuted_by),
            "missing_check_ids": list(self.missing_check_ids),
        }


@dataclass(frozen=True, slots=True)
class CriteriaEvaluation:
    """The whole-slice verdict: criteria, questions and the scenario result.

    ``scenario_result`` is carried for the receipt and is never read when a claim
    status is computed (see :func:`evaluate_criteria`); ``overall_claim_status``
    is derived from the questions alone.
    """

    criteria: tuple[CriterionEvaluation, ...]
    questions: tuple[QuestionEvaluation, ...]
    scenario_result: str
    overall_claim_status: str

    def __post_init__(self) -> None:
        if self.scenario_result not in SCENARIO_RESULTS:
            raise ScienceCriteriaError(
                "unknown_scenario_result",
                f"{self.scenario_result!r} is not one of {list(SCENARIO_RESULTS)}",
            )
        if self.overall_claim_status not in CRITERIA_STATUSES:
            raise ScienceCriteriaError(
                "unknown_claim_status", f"{self.overall_claim_status!r} is not a claim status"
            )

    @property
    def science_claim_supported(self) -> bool:
        """Whether the science claim is supported — independent of ``scenario_result``."""
        return self.overall_claim_status == "supported"

    def criterion(self, criterion_id: str) -> CriterionEvaluation:
        """Return the evaluation for *criterion_id* or raise."""
        for row in self.criteria:
            if row.criterion_id == criterion_id:
                return row
        raise ScienceCriteriaError("RJ-01-unknown-criterion", f"{criterion_id!r} was not evaluated")

    def question(self, question_id: str) -> QuestionEvaluation:
        """Return the evaluation for *question_id* or raise."""
        for row in self.questions:
            if row.question_id == question_id:
                return row
        raise ScienceCriteriaError("unknown_question", f"{question_id!r} was not evaluated")

    def status_rows(self) -> list[dict[str, Any]]:
        """Return the receipt rows (scenario result and claim status separate)."""
        return [
            {
                "row_id": row.criterion_id,
                "scenario_result": self.scenario_result,
                "claim_status": row.claim_status,
                "reason": row.reason,
                "applied_rules": list(row.applied_rules),
            }
            for row in self.criteria
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BUNDLE_SCHEMA,
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "criteria_regime": CRITERIA_REGIME,
            "criteria_version": CRITERIA_VERSION,
            "criteria_digest": criteria_definition_digest(),
            "scenario_result": self.scenario_result,
            "overall_claim_status": self.overall_claim_status,
            "criteria": [row.to_dict() for row in self.criteria],
            "questions": [row.to_dict() for row in self.questions],
        }


# ── evaluation ─────────────────────────────────────────────────────────────


def evaluate_criterion(
    criterion_id: str,
    record: CriterionEvidence | None,
) -> CriterionEvaluation:
    """Derive one criterion's four-valued status from digest-bound evidence.

    Args:
        criterion_id: A frozen criterion id.
        record: The criterion's evidence record, or ``None`` when nothing was
            produced (``DG-01``: ``not_evaluated``, never ``refuted``).

    Returns:
        The criterion verdict with the applied rule ids.
    """
    row = criterion_row(criterion_id)
    if record is None:
        return CriterionEvaluation(
            criterion_id=row.criterion_id,
            claim_status="not_evaluated",
            reason=REASON_FOR_STATUS["not_evaluated"],
            evidence_status=None,
            validator_id=None,
            evidence_digest=None,
            missing_check_ids=("not_executed",),
            applied_rules=("DG-01-evidence-absent",),
            notes=("no evidence record exists for this criterion",),
        )
    if record.criterion_id != row.criterion_id:
        raise ScienceCriteriaError(
            "RJ-01-unknown-criterion",
            f"record {record.criterion_id!r} cannot evaluate {row.criterion_id!r}",
        )
    if record.validator_id not in row.validator_ids:
        raise ScienceCriteriaError(
            "RJ-02-foreign-validator",
            f"{row.criterion_id}: {record.validator_id!r} is not one of {list(row.validator_ids)}",
        )
    if record.validator_version != VALIDATOR_VERSION:
        raise ScienceCriteriaError(
            "RJ-03-validator-version-drift",
            f"{row.criterion_id}: version {record.validator_version!r} is not "
            f"the frozen {VALIDATOR_VERSION!r}",
        )
    if record.status == "refuted" and not row.refutable:
        raise ScienceCriteriaError(
            "RJ-08-refuted-not-allowed",
            f"{row.criterion_id} is not refutable; a contradiction here is missing evidence",
        )
    if record.status in ("inconclusive", "not_evaluated") and not record.missing_check_ids:
        raise ScienceCriteriaError(
            "RJ-09-unreasoned-status",
            f"{row.criterion_id}: a {record.status} record must name a missing check",
        )

    applied: list[str] = []
    if record.status in ("supported", "refuted"):
        if not record.source_digests:
            return CriterionEvaluation(
                criterion_id=row.criterion_id,
                claim_status="inconclusive",
                reason=REASON_FOR_STATUS["inconclusive"],
                evidence_status=record.status,
                validator_id=record.validator_id,
                evidence_digest=record.evidence_digest,
                missing_check_ids=("artifact_missing",),
                applied_rules=("DG-04-source-not-digested",),
                notes=(f"a {record.status} verdict carries no digested source",),
            )
        if record.missing_check_ids:
            return CriterionEvaluation(
                criterion_id=row.criterion_id,
                claim_status="inconclusive",
                reason=REASON_FOR_STATUS["inconclusive"],
                evidence_status=record.status,
                validator_id=record.validator_id,
                evidence_digest=record.evidence_digest,
                missing_check_ids=record.missing_check_ids,
                applied_rules=("DG-03-partial-evidence",),
                notes=(f"a {record.status} verdict also declares blocking missing checks",),
            )

    return CriterionEvaluation(
        criterion_id=row.criterion_id,
        claim_status=record.status,
        reason=REASON_FOR_STATUS[record.status],
        evidence_status=record.status,
        validator_id=record.validator_id,
        evidence_digest=record.evidence_digest,
        missing_check_ids=record.missing_check_ids,
        applied_rules=tuple(applied),
    )


def _combine_question(
    row: QuestionRow,
    criteria: Mapping[str, CriterionEvaluation],
) -> QuestionEvaluation:
    statuses = {
        criterion_id: criteria[criterion_id].claim_status for criterion_id in row.required_criteria
    }
    refuted_by = tuple(
        criterion_id
        for criterion_id in row.refuting_criteria
        if statuses[criterion_id] == "refuted"
    )
    blocked_by = tuple(
        criterion_id
        for criterion_id in row.required_criteria
        if statuses[criterion_id] != "supported"
    )
    missing: list[str] = []
    for criterion_id in blocked_by:
        for check_id in criteria[criterion_id].missing_check_ids:
            if check_id not in missing:
                missing.append(check_id)
    if refuted_by:
        return QuestionEvaluation(
            question_id=row.question_id,
            claim_status="refuted",
            reason=REASON_FOR_STATUS["refuted"],
            blocked_by=blocked_by,
            refuted_by=refuted_by,
            missing_check_ids=tuple(missing),
        )
    if not blocked_by:
        return QuestionEvaluation(
            question_id=row.question_id,
            claim_status="supported",
            reason=REASON_FOR_STATUS["supported"],
        )
    if any(status in ("supported", "inconclusive", "refuted") for status in statuses.values()):
        return QuestionEvaluation(
            question_id=row.question_id,
            claim_status="inconclusive",
            reason=REASON_FOR_STATUS["inconclusive"],
            blocked_by=blocked_by,
            missing_check_ids=tuple(missing),
        )
    return QuestionEvaluation(
        question_id=row.question_id,
        claim_status="not_evaluated",
        reason=REASON_FOR_STATUS["not_evaluated"],
        blocked_by=blocked_by,
        missing_check_ids=tuple(missing),
    )


def _combine_overall(questions: Sequence[QuestionEvaluation]) -> str:
    statuses = [row.claim_status for row in questions]
    if "refuted" in statuses:
        return "refuted"
    if statuses and all(status == "supported" for status in statuses):
        return "supported"
    if any(status in ("supported", "inconclusive") for status in statuses):
        return "inconclusive"
    return "not_evaluated"


def evaluate_criteria(
    records: Sequence[CriterionEvidence],
    *,
    scenario_result: ScenarioResult | str,
) -> CriteriaEvaluation:
    """Evaluate the frozen table against digest-bound records.

    ``scenario_result`` records whether the execution scenario completed with
    valid artifacts.  It is validated and copied into the result, and it is
    **not** an input to any claim status: the same records give the same criteria
    and question verdicts for ``PASS``, ``FAIL`` and ``NOT_VERIFIED``.

    Raises:
        ScienceCriteriaError: duplicate records (``RJ-07``), an unknown criterion
            (``RJ-01``), an unknown scenario result, or any record-level violation
            raised by :func:`evaluate_criterion`.
    """
    if scenario_result not in SCENARIO_RESULTS:
        raise ScienceCriteriaError(
            "unknown_scenario_result",
            f"{scenario_result!r} is not one of {list(SCENARIO_RESULTS)}",
        )
    by_criterion: dict[str, CriterionEvidence] = {}
    for record in records:
        criterion_row(record.criterion_id)
        if record.criterion_id in by_criterion:
            raise ScienceCriteriaError(
                "RJ-07-duplicate-record",
                f"two evidence records claim {record.criterion_id!r}",
            )
        by_criterion[record.criterion_id] = record
    criteria = tuple(
        evaluate_criterion(row.criterion_id, by_criterion.get(row.criterion_id)) for row in CRITERIA
    )
    by_id = {row.criterion_id: row for row in criteria}
    questions = tuple(_combine_question(row, by_id) for row in QUESTION_ROWS)
    return CriteriaEvaluation(
        criteria=criteria,
        questions=questions,
        scenario_result=str(scenario_result),
        overall_claim_status=_combine_overall(questions),
    )


_BUNDLE_KEYS: Final[tuple[str, ...]] = (
    "schema",
    "schema_version",
    "fixture_id",
    "constructed",
    "real_run",
    "scenario_result",
    "records",
    "expected",
    "notes",
)


def evaluate_bundle(payload: Any) -> CriteriaEvaluation:
    """Parse a criteria-evaluation bundle and evaluate it.

    The bundle is the fixture/receipt shape used by ADR-008 samples; unknown
    fields are rejected so a result payload cannot smuggle a verdict in.
    """
    try:
        data = require_mapping(payload, "criteria_bundle")
        reject_unknown_keys(data, _BUNDLE_KEYS, "criteria_bundle")
    except SchemaViolationError as error:
        raise ScienceCriteriaError("RJ-06-unknown-field", f"criteria_bundle: {error}") from error
    if data.get("schema") != BUNDLE_SCHEMA:
        raise ScienceCriteriaError(
            "unknown_bundle_schema", f"criteria bundle schema must be {BUNDLE_SCHEMA!r}"
        )
    if data.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ScienceCriteriaError(
            "unknown_bundle_schema",
            f"criteria bundle schema_version must be {BUNDLE_SCHEMA_VERSION}",
        )
    try:
        raw_records = require_sequence(data.get("records", ()), "criteria_bundle.records")
    except SchemaViolationError as error:
        raise ScienceCriteriaError("RJ-11-invalid-shape", f"criteria_bundle: {error}") from error
    records = tuple(
        CriterionEvidence.from_dict(item, f"criteria_bundle.records[{index}]")
        for index, item in enumerate(raw_records)
    )
    return evaluate_criteria(records, scenario_result=str(data.get("scenario_result", "")))


# ── frozen definition digest ───────────────────────────────────────────────


def criteria_definition_payload() -> dict[str, Any]:
    """Return the canonical, digestible definition of the frozen table."""
    return {
        "schema": CRITERIA_SCHEMA,
        "version": CRITERIA_VERSION,
        "regime": CRITERIA_REGIME,
        "validator_version": VALIDATOR_VERSION,
        "thresholds": [FROZEN_THRESHOLDS[name].to_dict() for name in sorted(FROZEN_THRESHOLDS)],
        "downgrade_rules": [row.to_dict() for row in DEGRADATION_RULES],
        "rejection_rules": [row.to_dict() for row in REJECTION_RULES],
        "criteria": [row.to_dict() for row in CRITERIA],
        "questions": [row.to_dict() for row in QUESTION_ROWS],
    }


def criteria_definition_digest() -> str:
    """Return the bare sha256 of the canonical criteria definition.

    The ADR records this value; changing any criterion, threshold direction,
    validator set or downgrade rule changes it, so an inline "just for this run"
    edit is visible in the signed document.
    """
    return digest_evidence(criteria_definition_payload())
