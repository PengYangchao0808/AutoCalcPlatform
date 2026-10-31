"""M1-05: ADR-008 frozen Diels-Alder criteria, downgrade rules and samples.

The test file is the guard for four independent claims:

1. the machine table (:mod:`cccp.analysis.research.science_criteria`) and the
   ADR-008 markdown tables agree **row for row** — prose and code cannot drift;
2. thresholds come from the validator constants (never invented per row) and
   their direction is frozen;
3. missing evidence degrades to ``not_evaluated``/``inconclusive`` and never to
   ``refuted``; an execution-completed scenario with no evidence is
   ``not_evaluated`` while its ``scenario_result`` stays ``PASS``;
4. a result payload cannot bypass the criteria (override keys, foreign
   validators, version drift, tampered digests, refuting a non-refutable row).

Every fixture under ``tests/fixtures/acp2/science/diels_alder/criteria/`` is
constructed (``constructed: true``, ``real_run: false``) and digested; no real
QC run, TS, IRC or energy value exists.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from acp_services.runtime.evidence_projection import ClaimProjection
from cccp.analysis.research import evidence as evidence_module
from cccp.analysis.research import science_criteria as criteria
from cccp.analysis.research import validators as validators_module
from cccp.analysis.research.evidence import (
    ArtifactSourceRef,
    Claim,
    ClaimScope,
    Digest,
    EvidenceMissingCheck,
    EvidenceValidator,
)
from cccp.analysis.research.question import CLAIM_STATUSES as CONTRACT_CLAIM_STATUSES

REPO_ROOT = Path(__file__).resolve().parents[1]
ADR_PATH = REPO_ROOT / criteria.CRITERIA_DOCUMENT
CRITERIA_MODULE = REPO_ROOT / "src/cccp/analysis/research/science_criteria.py"
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/acp2/science/diels_alder/criteria"
MANIFEST_PATH = FIXTURE_DIR / "fixture-manifest.json"

#: The digest the ADR records.  Freezing it here as well means a silent edit of
#: any criterion/threshold/downgrade row fails three places at once.
FROZEN_CRITERIA_DIGEST = "f89f73da4178f4db35b5eb2b8f3958a4cad8bacc5c82490ccfc6777a2c3449d3"

PATH_QUESTION = "question.diels_alder.path_connectivity.v1"
ENERGY_QUESTION = "question.diels_alder.activation_energy.v1"

C01 = "DA-C01-ts-single-imaginary-mode"
C02 = "DA-C02-ts-mode-mapping"
C03 = "DA-C03-frequency-evidence"
C04 = "DA-C04-irc-bidirectional"
C05 = "DA-C05-endpoint-association"
C06 = "DA-C06-energy-comparability"
C07 = "DA-C07-reactant-stoichiometry"


# ---------------------------------------------------------------------------
# ADR table parsing
# ---------------------------------------------------------------------------


def _adr_text() -> str:
    return ADR_PATH.read_text(encoding="utf-8")


def _split(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_rows(prefix: str, width: int) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in _adr_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = _split(line)
        if len(cells) != width or not cells[0].startswith(f"`{prefix}"):
            continue
        rows[cells[0].strip("`")] = cells[1:]
    return rows


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _codes(cell: str) -> list[str]:
    if cell in ("—", "-", ""):
        return []
    return [item.strip().strip("`") for item in cell.split(", ")]


# ---------------------------------------------------------------------------
# 1. the ADR and the machine table agree
# ---------------------------------------------------------------------------


def test_criteria_rows_match_the_adr_row_for_row() -> None:
    documented = _table_rows("DA-C", 10)
    assert documented, "no criterion rows parsed from ADR-008"
    assert set(documented) == {row.criterion_id for row in criteria.CRITERIA}
    for row in criteria.CRITERIA:
        area, acceptance, requirement, evidence, validators, thresholds, refutable, steps, rules = (
            documented[row.criterion_id]
        )
        assert area == row.area, row.criterion_id
        assert acceptance.strip("`") == row.acceptance_row, row.criterion_id
        assert _normalize(requirement) == _normalize(row.requirement), row.criterion_id
        assert _codes(evidence) == list(row.required_evidence), row.criterion_id
        assert _codes(validators) == list(row.validator_ids), row.criterion_id
        if row.threshold_names:
            assert thresholds.split(", ") == [
                f"`{token}`" for token in criteria.threshold_tokens(row.threshold_names)
            ], row.criterion_id
        else:
            assert thresholds == "—", row.criterion_id
        assert refutable == ("yes" if row.refutable else "no"), row.criterion_id
        assert _codes(steps) == list(row.recipe_steps), row.criterion_id
        assert _codes(rules) == list(row.downgrade_rules), row.criterion_id


def test_status_rules_match_the_adr_row_for_row() -> None:
    documented = _table_rows("DA-C", 5)
    assert set(documented) == {row.criterion_id for row in criteria.CRITERIA}
    for row in criteria.CRITERIA:
        supported, refuted, inconclusive, not_evaluated = documented[row.criterion_id]
        assert _normalize(supported) == _normalize(row.supported), row.criterion_id
        assert _normalize(refuted) == _normalize(row.refuted), row.criterion_id
        assert _normalize(inconclusive) == _normalize(row.inconclusive), row.criterion_id
        assert _normalize(not_evaluated) == _normalize(row.not_evaluated), row.criterion_id


def test_question_rows_match_the_adr_row_for_row() -> None:
    documented = _table_rows("question.", 7)
    assert set(documented) == {row.question_id for row in criteria.QUESTION_ROWS}
    for row in criteria.QUESTION_ROWS:
        required, refuting, supported, refuted, inconclusive, not_evaluated = documented[
            row.question_id
        ]
        assert _codes(required) == list(row.required_criteria), row.question_id
        assert _codes(refuting) == list(row.refuting_criteria), row.question_id
        assert _normalize(supported) == _normalize(row.supported), row.question_id
        assert _normalize(refuted) == _normalize(row.refuted), row.question_id
        assert _normalize(inconclusive) == _normalize(row.inconclusive), row.question_id
        assert _normalize(not_evaluated) == _normalize(row.not_evaluated), row.question_id


def test_downgrade_rules_match_the_adr_row_for_row() -> None:
    documented = _table_rows("DG-", 5)
    assert set(documented) == {row.rule_id for row in criteria.DEGRADATION_RULES}
    for row in criteria.DEGRADATION_RULES:
        condition, result, never, reason = documented[row.rule_id]
        assert _normalize(condition) == _normalize(row.condition), row.rule_id
        assert result.strip("`") == row.result, row.rule_id
        assert _codes(never) == list(row.never), row.rule_id
        assert _normalize(reason) == _normalize(row.reason), row.rule_id


def test_rejection_rules_match_the_adr_row_for_row() -> None:
    documented = _table_rows("RJ-", 2)
    assert set(documented) == {row.rule_id for row in criteria.REJECTION_RULES}
    for row in criteria.REJECTION_RULES:
        (condition,) = documented[row.rule_id]
        assert _normalize(condition) == _normalize(row.condition), row.rule_id


def test_criteria_definition_digest_is_frozen_in_the_adr() -> None:
    digest = criteria.criteria_definition_digest()
    assert digest == FROZEN_CRITERIA_DIGEST
    assert digest in _adr_text(), "the ADR must record the frozen criteria digest"


def test_adr_sample_table_lists_every_registered_bundle() -> None:
    documented: dict[str, str] = {}
    for line in _adr_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = _split(line)
        if len(cells) != 4 or not cells[1].startswith("`bundle."):
            continue
        documented[cells[0].strip("`")] = cells[1].strip("`")
    manifest = _manifest()
    assert documented == manifest["fixture_files"], (
        "ADR-008 §13 and the fixture manifest must list the same samples"
    )


def test_adr_declares_both_environment_rows_and_the_scope_refusals() -> None:
    text = _adr_text()
    for token in ("`DA-ENV-LOCAL`", "`DA-ENV-LSF`", "NOT_VERIFIED", "endo/exo"):
        assert token in text, token
    local = text.index("`DA-ENV-LOCAL`")
    lsf = text.index("`DA-ENV-LSF`")
    assert local < lsf


# ---------------------------------------------------------------------------
# 2. thresholds: single owner, frozen direction
# ---------------------------------------------------------------------------


def test_every_threshold_is_owned_by_a_frozen_validator_constant() -> None:
    owners = {
        "validators.ATOM_COUNT_TOLERANCE": validators_module.ATOM_COUNT_TOLERANCE,
        "validators.COORDINATE_ABS_TOLERANCE_ANGSTROM": (
            validators_module.COORDINATE_ABS_TOLERANCE_ANGSTROM
        ),
        "validators.IMAGINARY_MODE_THRESHOLD_CM1": validators_module.IMAGINARY_MODE_THRESHOLD_CM1,
        "validators.LOW_FREQUENCY_THRESHOLD_CM1": validators_module.LOW_FREQUENCY_THRESHOLD_CM1,
    }
    for name, row in criteria.FROZEN_THRESHOLDS.items():
        assert row.owner in owners, (name, row.owner)
        assert row.value == float(owners[row.owner]), name
        assert row.constructed is False, name


def test_threshold_direction_is_frozen() -> None:
    imaginary = criteria.FROZEN_THRESHOLDS["imaginary_mode_threshold_cm1"].value
    low = criteria.FROZEN_THRESHOLDS["low_frequency_threshold_cm1"].value
    coordinate = criteria.FROZEN_THRESHOLDS["coordinate_abs_tolerance_angstrom"].value
    atom_count = criteria.FROZEN_THRESHOLDS["atom_count_tolerance"].value
    # A soft real mode threshold must sit strictly above the imaginary crossing.
    assert low > imaginary
    # A geometry tolerance must be positive but far below chemical significance.
    assert 0.0 < coordinate < 0.01
    # An atom-count tolerance of anything but zero would hide a real mismatch.
    assert atom_count == 0.0


def test_every_criterion_cites_a_registered_validator_at_the_frozen_version() -> None:
    registered = {
        value
        for name, value in vars(validators_module).items()
        if name.endswith("_VALIDATOR_ID") and isinstance(value, str)
    }
    for row in criteria.CRITERIA:
        assert row.validator_ids, row.criterion_id
        for validator_id in row.validator_ids:
            assert validator_id in registered, (row.criterion_id, validator_id)
    assert validators_module.VALIDATOR_VERSION == "1.0.0"
    assert "`1.0.0`" in _adr_text() or "1.0.0" in _adr_text()


def test_every_cited_threshold_token_is_in_the_adr() -> None:
    text = _adr_text()
    for row in criteria.CRITERIA:
        for token in criteria.threshold_tokens(row.threshold_names):
            assert f"`{token}`" in text, (row.criterion_id, token)


def test_criteria_cover_the_five_required_areas() -> None:
    assert {row.area for row in criteria.CRITERIA} == {
        "ts",
        "frequency",
        "irc",
        "endpoint",
        "energy",
    }
    # The M4-04 acceptance rows must each be covered by at least one criterion.
    assert {row.acceptance_row for row in criteria.CRITERIA} == {
        "DA-08-real-ts",
        "DA-09-real-irc",
        "DA-10-real-energy",
    }


def test_non_refutable_rows_are_declared_as_such() -> None:
    refutable = {row.criterion_id: row.refutable for row in criteria.CRITERIA}
    assert refutable[C03] is False, "a missing Hessian is never a contradiction"
    assert refutable[C04] is False, "a one-sided IRC is never a contradiction"
    for question in criteria.QUESTION_ROWS:
        for criterion_id in question.refuting_criteria:
            assert refutable[criterion_id] is True


# ---------------------------------------------------------------------------
# 3. fixtures: constructed, digested, and matching their declared expectation
# ---------------------------------------------------------------------------


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _bundle(key: str) -> dict:
    name = _manifest()["fixture_files"][key]
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _bundles() -> list[tuple[str, dict]]:
    manifest = _manifest()
    return [
        (key, json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))
        for key, name in sorted(manifest["fixture_files"].items())
    ]


def test_fixture_manifest_is_constructed_and_digested() -> None:
    manifest = _manifest()
    assert manifest["constructed"] is True
    assert manifest["real_run"] is False
    assert manifest["adr"] == criteria.CRITERIA_DOCUMENT
    assert manifest["criteria_schema"] == criteria.CRITERIA_SCHEMA
    assert manifest["criteria_version"] == criteria.CRITERIA_VERSION
    assert manifest["criteria_definition_digest"] == criteria.criteria_definition_digest()
    assert manifest["validator_version"] == validators_module.VALIDATOR_VERSION
    files = manifest["fixture_files"]
    assert set(files.values()) == set(manifest["file_digests"])
    for name, digest in manifest["file_digests"].items():
        assert hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest() == digest, name
    on_disk = {path.name for path in FIXTURE_DIR.glob("bundle.*.json")}
    assert on_disk == set(files.values()), "every bundle must be registered with a digest"


def test_every_bundle_is_labelled_and_digest_bound() -> None:
    for key, bundle in _bundles():
        assert bundle["schema"] == criteria.BUNDLE_SCHEMA, key
        assert bundle["schema_version"] == criteria.BUNDLE_SCHEMA_VERSION, key
        assert bundle["constructed"] is True, key
        assert bundle["real_run"] is False, key
        for record in bundle["records"]:
            # A record's digest is the real sha256 of its own payload; it is
            # never a fabricated constant.
            if "criteria_passed" in record["evidence"]:
                continue
            assert record["evidence_digest"] == criteria.digest_evidence(record["evidence"]), (
                key,
                record["criterion_id"],
            )


def test_every_bundle_matches_its_declared_expectation() -> None:
    keys = [key for key, _ in _bundles()]
    assert keys, "no ADR-008 criteria bundles were found"
    for key, bundle in _bundles():
        expected = bundle["expected"]
        if "error_rule" in expected:
            with pytest.raises(criteria.ScienceCriteriaError) as info:
                criteria.evaluate_bundle(bundle)
            assert info.value.rule == expected["error_rule"], key
            continue
        evaluation = criteria.evaluate_bundle(bundle)
        assert evaluation.scenario_result == bundle["scenario_result"], key
        assert evaluation.overall_claim_status == expected["overall_claim_status"], key
        for criterion_id, status in expected["criteria"].items():
            assert evaluation.criterion(criterion_id).claim_status == status, (key, criterion_id)
        for question_id, status in expected["questions"].items():
            assert evaluation.question(question_id).claim_status == status, (key, question_id)


# ---------------------------------------------------------------------------
# 4. missing evidence degrades honestly
# ---------------------------------------------------------------------------


def test_missing_frequency_is_not_evaluated_never_refuted() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("missing_frequency"))
    for criterion_id in (C01, C02, C03):
        row = evaluation.criterion(criterion_id)
        assert row.claim_status == "not_evaluated", criterion_id
        assert row.evidence_status is None, criterion_id
        assert "DG-01-evidence-absent" in row.applied_rules, criterion_id
        assert "not_executed" in row.missing_check_ids, criterion_id
    assert all(row.claim_status != "refuted" for row in evaluation.criteria)
    assert evaluation.question(PATH_QUESTION).claim_status == "inconclusive"
    assert evaluation.question(ENERGY_QUESTION).claim_status == "inconclusive"


def test_completed_execution_without_evidence_is_no_conclusion() -> None:
    bundle = _bundle("no_conclusion")
    assert bundle["scenario_result"] == "PASS"
    assert bundle["records"] == []
    evaluation = criteria.evaluate_bundle(bundle)
    assert evaluation.scenario_result == "PASS"
    assert {row.claim_status for row in evaluation.criteria} == {"not_evaluated"}
    assert evaluation.question(PATH_QUESTION).claim_status == "not_evaluated"
    assert evaluation.question(ENERGY_QUESTION).claim_status == "not_evaluated"
    assert evaluation.overall_claim_status == "not_evaluated"
    assert evaluation.science_claim_supported is False
    rows = evaluation.status_rows()
    assert rows and all(row["scenario_result"] == "PASS" for row in rows)
    assert all(row["claim_status"] != "supported" for row in rows)


def test_failed_step_is_not_a_scientific_refutation() -> None:
    row = criteria.evaluate_criterion(C04, None)
    assert row.claim_status == "not_evaluated"
    assert row.applied_rules == ("DG-01-evidence-absent",)
    rule = {item.rule_id: item for item in criteria.DEGRADATION_RULES}["DG-02-step-not-completed"]
    assert rule.result == "not_evaluated"
    assert rule.never == ("refuted",)


def test_supported_without_a_digested_source_is_downgraded() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("supported_without_source"))
    row = evaluation.criterion(C01)
    assert row.claim_status == "inconclusive"
    assert row.evidence_status == "supported"
    assert row.applied_rules == ("DG-04-source-not-digested",)
    assert row.missing_check_ids == ("artifact_missing",)


# ---------------------------------------------------------------------------
# 5. scenario_result never decides claim_status
# ---------------------------------------------------------------------------


def test_scenario_result_never_changes_a_claim_status() -> None:
    payload = _bundle("all_supported")
    records = [criteria.CriterionEvidence.from_dict(item) for item in payload["records"]]
    baseline = criteria.evaluate_criteria(records, scenario_result="PASS")
    assert baseline.overall_claim_status == "supported"
    baseline_rows = [(row.criterion_id, row.claim_status) for row in baseline.criteria]
    baseline_questions = [(row.question_id, row.claim_status) for row in baseline.questions]
    for scenario in ("FAIL", "NOT_VERIFIED"):
        other = criteria.evaluate_criteria(records, scenario_result=scenario)
        assert other.scenario_result == scenario
        assert [(row.criterion_id, row.claim_status) for row in other.criteria] == baseline_rows
        assert [
            (row.question_id, row.claim_status) for row in other.questions
        ] == baseline_questions
        assert other.overall_claim_status == baseline.overall_claim_status
        assert other.science_claim_supported is True
    # ... and a PASS scenario with no evidence is still not a supported claim.
    empty = criteria.evaluate_criteria([], scenario_result="PASS")
    assert empty.overall_claim_status == "not_evaluated"


def test_unknown_scenario_result_is_rejected() -> None:
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_criteria([], scenario_result="PASSED")
    assert info.value.rule == "unknown_scenario_result"


# ---------------------------------------------------------------------------
# 6. aggregation: a prerequisite refutation is not a question refutation
# ---------------------------------------------------------------------------


def test_ts_refutation_does_not_refute_connectivity() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("ts_two_imaginary_modes"))
    assert evaluation.criterion(C01).claim_status == "refuted"
    path = evaluation.question(PATH_QUESTION)
    assert path.claim_status == "inconclusive"
    assert path.refuted_by == ()
    assert C01 in path.blocked_by
    assert evaluation.question(ENERGY_QUESTION).claim_status == "inconclusive"
    assert evaluation.overall_claim_status == "inconclusive"


def test_endpoint_refutation_refutes_connectivity() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("endpoint_mismatch"))
    assert evaluation.criterion(C05).claim_status == "refuted"
    path = evaluation.question(PATH_QUESTION)
    assert path.claim_status == "refuted"
    assert path.refuted_by == (C05,)
    assert evaluation.question(ENERGY_QUESTION).claim_status == "supported"
    assert evaluation.overall_claim_status == "refuted"


def test_single_irc_direction_is_inconclusive_but_barrier_stands() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("irc_one_direction"))
    assert evaluation.criterion(C04).claim_status == "inconclusive"
    assert evaluation.criterion(C05).claim_status == "inconclusive"
    assert evaluation.question(PATH_QUESTION).claim_status == "inconclusive"
    # The barrier does not need IRC, so a missing IRC direction cannot block it.
    assert evaluation.question(ENERGY_QUESTION).claim_status == "supported"
    assert evaluation.overall_claim_status == "inconclusive"


def test_energy_condition_mismatch_only_refutes_the_barrier() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("energy_conditions_mismatch"))
    assert evaluation.criterion(C06).claim_status == "refuted"
    assert evaluation.question(ENERGY_QUESTION).claim_status == "refuted"
    assert evaluation.question(ENERGY_QUESTION).refuted_by == (C06,)
    assert evaluation.question(PATH_QUESTION).claim_status == "supported"


def test_all_supported_is_the_only_supported_overall_state() -> None:
    evaluation = criteria.evaluate_bundle(_bundle("all_supported"))
    assert all(row.claim_status == "supported" for row in evaluation.criteria)
    assert evaluation.question(PATH_QUESTION).claim_status == "supported"
    assert evaluation.question(ENERGY_QUESTION).claim_status == "supported"
    assert evaluation.overall_claim_status == "supported"
    assert evaluation.science_claim_supported is True


# ---------------------------------------------------------------------------
# 7. a result payload cannot bypass the criteria
# ---------------------------------------------------------------------------


def _record(criterion_id: str = C01, **overrides) -> dict:
    payload = {
        "criterion_id": criterion_id,
        "claim_id": "c-ts-exactly-one-imaginary-mode",
        "status": "supported",
        "validator_id": "check.ts_saddle_candidate",
        "validator_version": validators_module.VALIDATOR_VERSION,
        "evidence": {"imaginary_mode_count": 1, "mode_count": 45},
        "source_digests": ["a" * 64],
        "missing_check_ids": [],
    }
    payload["evidence_digest"] = criteria.digest_evidence(payload["evidence"])
    payload.update(overrides)
    return payload


def test_status_override_key_in_the_evidence_payload_is_refused() -> None:
    evidence = {"criteria_passed": True, "imaginary_mode_count": 1}
    payload = _record(evidence=evidence)
    payload["evidence_digest"] = criteria.digest_evidence(evidence)
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.CriterionEvidence.from_dict(payload)
    assert info.value.rule == "RJ-05-status-override"


def test_nested_verdict_key_is_refused() -> None:
    evidence = {"forward": {"claim_status": "supported"}}
    payload = _record(evidence=evidence)
    payload["evidence_digest"] = criteria.digest_evidence(evidence)
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.CriterionEvidence.from_dict(payload)
    assert info.value.rule == "RJ-05-status-override"


def test_unknown_record_field_is_refused() -> None:
    payload = _record()
    payload["criteria_passed"] = True
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.CriterionEvidence.from_dict(payload)
    assert info.value.rule == "RJ-06-unknown-field"


def test_tampered_evidence_payload_is_refused() -> None:
    payload = _record()
    payload["evidence"] = {"imaginary_mode_count": 1, "mode_count": 44}
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.CriterionEvidence.from_dict(payload)
    assert info.value.rule == "RJ-04-evidence-not-digest-bound"


def test_foreign_validator_cannot_evaluate_a_criterion() -> None:
    record = criteria.CriterionEvidence.from_dict(_record(validator_id="check.method_consistency"))
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_criterion(C01, record)
    assert info.value.rule == "RJ-02-foreign-validator"


def test_validator_version_drift_is_rejected() -> None:
    record = criteria.CriterionEvidence.from_dict(_record(validator_version="0.9.0"))
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_criterion(C01, record)
    assert info.value.rule == "RJ-03-validator-version-drift"


def test_a_non_refutable_criterion_cannot_be_refuted() -> None:
    record = criteria.CriterionEvidence.from_dict(
        {
            "criterion_id": C03,
            "claim_id": "c-frequency-hessian-present",
            "status": "refuted",
            "validator_id": "check.frequency_evidence",
            "validator_version": validators_module.VALIDATOR_VERSION,
            "evidence": {"frequency_count": 0},
            "evidence_digest": criteria.digest_evidence({"frequency_count": 0}),
            "source_digests": ["b" * 64],
            "missing_check_ids": [],
        }
    )
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_criterion(C03, record)
    assert info.value.rule == "RJ-08-refuted-not-allowed"


def test_an_unreasoned_inconclusive_record_is_rejected() -> None:
    record = criteria.CriterionEvidence.from_dict(
        _record(status="inconclusive", source_digests=[], missing_check_ids=[])
    )
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_criterion(C01, record)
    assert info.value.rule == "RJ-09-unreasoned-status"


def test_duplicate_and_unknown_records_are_rejected() -> None:
    record = criteria.CriterionEvidence.from_dict(_record())
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_criteria([record, record], scenario_result="FAIL")
    assert info.value.rule == "RJ-07-duplicate-record"
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.criterion_row("DA-C99-not-frozen")
    assert info.value.rule == "RJ-01-unknown-criterion"


def test_bundle_with_an_unknown_field_is_rejected() -> None:
    payload = dict(_bundle("all_supported"))
    payload["claim_status"] = "supported"
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.evaluate_bundle(payload)
    assert info.value.rule == "RJ-06-unknown-field"


# ---------------------------------------------------------------------------
# 8. the evidence layer is the input, not a parallel model
# ---------------------------------------------------------------------------


def _claim(status: str = "supported") -> Claim:
    digest = Digest.from_bytes(b"constructed ADR-008 frequency evidence")
    missing = ()
    if status in ("inconclusive", "not_evaluated"):
        missing = (
            EvidenceMissingCheck(
                check_id="frequency_not_computed",
                reason="constructed: no Hessian evidence exists",
            ),
        )
    return Claim(
        claim_id="c-ts-exactly-one-imaginary-mode",
        statement="The frequency result contains exactly one imaginary mode.",
        status=status,
        scope=ClaimScope(
            subject="stationary_point_kind",
            applicability="conformer_specific",
            conditions="gas phase, wB97X-D4/def2-TZVP",
        ),
        source_refs=(
            ArtifactSourceRef(
                artifact_path="artifacts/ts_frequency.out",
                digest=digest,
                role="native_output",
                units="cm-1",
                quantity="harmonic_frequencies",
            ),
        ),
        missing_checks=missing,
        validator=EvidenceValidator(
            validator_id="check.ts_saddle_candidate",
            validator_version=validators_module.VALIDATOR_VERSION,
            kind="deterministic",
        ),
    )


def test_claim_adapter_carries_the_validator_sources_and_digest() -> None:
    claim = _claim()
    record = criteria.criterion_evidence_from_claim(claim, C01)
    assert record.status == "supported"
    assert record.validator_id == "check.ts_saddle_candidate"
    assert record.validator_version == validators_module.VALIDATOR_VERSION
    assert record.source_digests == (claim.source_refs[0].digest.value,)
    assert record.evidence == {"claim": claim.to_dict()}
    assert record.evidence_digest == criteria.digest_evidence({"claim": claim.to_dict()})
    evaluation = criteria.evaluate_criterion(C01, record)
    assert evaluation.claim_status == "supported"
    assert evaluation.evidence_digest == record.evidence_digest


def test_inconclusive_claim_adapts_with_its_missing_check() -> None:
    record = criteria.criterion_evidence_from_claim(_claim("inconclusive"), C01)
    assert record.missing_check_ids == ("frequency_not_computed",)
    evaluation = criteria.evaluate_criterion(C01, record)
    assert evaluation.claim_status == "inconclusive"
    assert evaluation.missing_check_ids == ("frequency_not_computed",)


def test_claim_without_a_validator_cannot_become_evidence() -> None:
    claim = Claim(
        claim_id="c-unattributed",
        statement="No validator produced this.",
        status="not_evaluated",
        scope=ClaimScope(subject="stationary_point_kind", applicability="universal"),
        missing_checks=(
            EvidenceMissingCheck(check_id="not_executed", reason="constructed: not executed"),
        ),
    )
    with pytest.raises(criteria.ScienceCriteriaError) as info:
        criteria.criterion_evidence_from_claim(claim, C01)
    assert info.value.rule == "evidence_not_attributable"


def test_status_vocabulary_matches_the_evidence_and_projection_layers() -> None:
    assert set(criteria.CRITERIA_STATUSES) == set(evidence_module.CLAIM_STATUSES)
    assert set(criteria.CRITERIA_STATUSES) == set(validators_module.CLAIM_STATUSES)
    assert set(criteria.CRITERIA_STATUSES) == set(CONTRACT_CLAIM_STATUSES)
    for status in sorted(criteria.CRITERIA_STATUSES):
        projection = ClaimProjection.from_claim(_claim(status))
        assert projection.status == status
    assert set(criteria.SCENARIO_RESULTS) == {"PASS", "FAIL", "NOT_VERIFIED"}
    assert not (set(criteria.SCENARIO_RESULTS) & set(criteria.CRITERIA_STATUSES))


# ---------------------------------------------------------------------------
# 9. isolation: the criteria module is pure science, not an execution path
# ---------------------------------------------------------------------------


def test_criteria_module_imports_no_execution_or_platform_module() -> None:
    tree = ast.parse(CRITERIA_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("acp", "acp_services", "subprocess", "cccp.calculation", "cccp.qc", "cccp.io")
    for module in sorted(imported):
        for prefix in forbidden:
            assert not (module == prefix or module.startswith(f"{prefix}.")), module
    assert imported, "the module must import its sibling science types"


def test_criteria_module_is_exported_from_the_research_package() -> None:
    import cccp.analysis.research as research

    assert research.science_criteria is criteria
    assert research.CRITERIA is criteria.CRITERIA
    assert "science_criteria" in research.__all__
    assert "evaluate_criteria" in research.__all__
