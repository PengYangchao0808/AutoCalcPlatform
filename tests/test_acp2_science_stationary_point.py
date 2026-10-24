"""A-11a: frozen opt/freq fixture and deterministic stationary-point validators.

The fixture is **constructed**: no optimization, frequency or QC subprocess ran
for this module.  Synthetic ORCA-shaped text exercises the existing frequency
parser, and the frozen ``TaskResult`` documents are built from that parse.  The
validators are pure functions, so every assertion below is deterministic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cccp.analysis.research import (
    ATOM_COUNT_TOLERANCE,
    COORDINATE_ABS_TOLERANCE_ANGSTROM,
    ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID,
    FREQUENCY_EVIDENCE_VALIDATOR_ID,
    FREQUENCY_MEMBERSHIP_VALIDATOR_ID,
    IMAGINARY_MODE_THRESHOLD_CM1,
    LOW_FREQUENCY_THRESHOLD_CM1,
    METHOD_CONSISTENCY_VALIDATOR_ID,
    OPTIMIZATION_CONVERGENCE_VALIDATOR_ID,
    STATIONARY_POINT_VALIDATOR_ID,
    VALIDATOR_VERSION,
    ClaimAssessment,
    validate_electronic_state_consistency,
    validate_frequency_evidence,
    validate_frequency_membership,
    validate_method_consistency,
    validate_optimization_convergence,
    validate_stationary_point,
)
from cccp.analysis.research.question import CLAIM_STATUSES
from cccp.calculation.frequency_parse import parse_frequency_log
from cccp.calculation.requests import TaskKind, TaskRequest, validate_request
from cccp.calculation.results import TaskResult

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "acp2" / "science" / "optfreq"
SYNTHETIC = FIXTURE / "synthetic"

#: Pinned independently of the manifest so a simultaneous fixture+manifest edit
#: cannot move the frozen structures silently.
EXPECTED_INITIAL_SHA256 = "1a21db8d08a35e5dcc961cf47ac2b688a5014dbcf8e35e7db34303ea81d376c7"
EXPECTED_OPTIMIZED_SHA256 = "d6c40b0505e559882ef4bf81f50953b5c205f851613ac5cfa99a54852016e7db"

SUPPORTED_CLAIM_IDS = (
    "c-optimization-converged",
    "c-frequency-belongs-to-optimized-structure",
    "c-frequency-hessian-present",
    "c-method-consistent",
    "c-electronic-state-consistent",
    "c-no-imaginary-modes",
    "c-stationary-point-minimum",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
        return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result(name: str) -> TaskResult:
    return TaskResult.from_dict(_load_json(SYNTHETIC / name))


def _result_from_payload(payload: dict[str, Any]) -> TaskResult:
    return TaskResult.from_dict(payload)


def _frozen_thresholds() -> dict[str, Any]:
    return {
        "source_module": "cccp.analysis.research.validators",
        "validator_version": VALIDATOR_VERSION,
        "claim_statuses": list(CLAIM_STATUSES),
        "low_frequency_threshold_cm1": LOW_FREQUENCY_THRESHOLD_CM1,
        "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
        "coordinate_abs_tolerance_angstrom": COORDINATE_ABS_TOLERANCE_ANGSTROM,
        "atom_count_tolerance": ATOM_COUNT_TOLERANCE,
    }


def _check_ids(claim: ClaimAssessment | None) -> set[str]:
    assert claim is not None
    return {check.check_id for check in claim.missing_checks}


def _mutated_minimum_frequency(**changes: Any) -> TaskResult:
    payload = _load_json(SYNTHETIC / "frequency-minimum-result.json")
    analysis = payload["payload"]["analysis"]
    frequencies = payload["frequencies"]
    mode_frequencies = analysis["mode_frequencies"]
    for index, mode_key in enumerate(("6", "7", "8")):
        if mode_key in changes:
            value = float(changes[mode_key])
            frequencies[index] = value
            analysis["frequencies"][index] = value
            mode_frequencies[mode_key] = value
    return _result_from_payload(payload)


# ── the fixture is frozen, constructed and self-consistent ─────────────────


def test_fixture_manifest_digests_and_thresholds_are_frozen() -> None:
    manifest = _load_json(FIXTURE / "fixture-manifest.json")
    assert manifest["schema"] == "acp2.science.fixture-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["fixture_id"] == "optfreq-water-wb97xd4-def2svp"
    assert manifest["constructed"] is True
    assert manifest["run_key"] is None
    assert manifest["live_validation_gap"]

    for relative, expected in manifest["file_digests"].items():
        assert _sha256_file(FIXTURE / relative) == expected, relative
    assert manifest["file_digests"]["initial.xyz"] == EXPECTED_INITIAL_SHA256
    assert manifest["file_digests"]["optimized.xyz"] == EXPECTED_OPTIMIZED_SHA256
    assert manifest["thresholds"] == _frozen_thresholds()


def test_source_references_and_scientific_checks_cite_the_frozen_constants() -> None:
    manifest = _load_json(FIXTURE / "fixture-manifest.json")
    references = _load_json(FIXTURE / "source-references.json")
    checks = _load_json(FIXTURE / "scientific-checks.json")

    assert references["constructed"] is True
    assert references["sources"]
    for source in references["sources"]:
        relative = source["artifact_path"]
        assert not Path(relative).is_absolute()
        assert _sha256_file(FIXTURE / relative) == source["digest"]["value"]
        assert source["digest"]["algorithm"] == "sha256"
        assert "units" in source

    assert checks["thresholds"] == manifest["thresholds"] == _frozen_thresholds()
    assert set(checks["thresholds"]["claim_statuses"]) == set(CLAIM_STATUSES)
    validators_by_claim = {entry["claim_id"]: entry["validator_id"] for entry in checks["checks"]}
    assert validators_by_claim["c-optimization-converged"] == (
        OPTIMIZATION_CONVERGENCE_VALIDATOR_ID
    )
    assert validators_by_claim["c-frequency-belongs-to-optimized-structure"] == (
        FREQUENCY_MEMBERSHIP_VALIDATOR_ID
    )
    assert validators_by_claim["c-frequency-hessian-present"] == (FREQUENCY_EVIDENCE_VALIDATOR_ID)
    assert validators_by_claim["c-method-consistent"] == METHOD_CONSISTENCY_VALIDATOR_ID
    assert validators_by_claim["c-electronic-state-consistent"] == (
        ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID
    )
    assert validators_by_claim["c-stationary-point-minimum"] == (STATIONARY_POINT_VALIDATOR_ID)
    assert checks["expected_outcomes"]["converged_synthetic_minimum"] == "supported"
    assert checks["expected_outcomes"]["unconverged_optimization"] == "refuted"
    assert checks["expected_outcomes"]["wrong_structure_frequency"] == "refuted"
    assert checks["expected_outcomes"]["absent_hessian"] == "inconclusive"


def test_requests_parse_with_the_authoritative_request_contract() -> None:
    optimize_request = TaskRequest.from_dict(_load_json(FIXTURE / "optimize-request.json"))
    frequency_request = TaskRequest.from_dict(_load_json(FIXTURE / "frequency-request.json"))
    validate_request(optimize_request)
    validate_request(frequency_request)

    assert optimize_request.task is TaskKind.OPTIMIZE
    assert frequency_request.task is TaskKind.FREQUENCY
    for request in (optimize_request, frequency_request):
        assert request.level.method == "wB97X-D4"
        assert request.level.basis == "def2-SVP"
        assert request.charge == 0
        assert request.multiplicity == 1
        assert request.structure is not None
        assert request.structure.symbols == ("O", "H", "H")
        assert request.electronic_state is not None
        assert request.electronic_state.state_id == "s0"


def test_synthetic_real_format_output_reproduces_the_frozen_frequency_result() -> None:
    text = (SYNTHETIC / "frequency-minimum.out").read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("# CONSTRUCTED SYNTHETIC OUTPUT")
    analysis = parse_frequency_log(SYNTHETIC / "frequency-minimum.out")
    assert analysis is not None
    assert analysis.frequencies == (1642.31, 3817.55, 3921.08)
    assert analysis.imaginary_frequencies == ()
    assert len(analysis.mode_vectors) == 9
    assert all(len(rows) == 3 for rows in analysis.mode_vectors.values())
    assert analysis.mode_ir_intensities == {6: 67.2, 7: 12.5, 8: 42.8}

    frozen = _result("frequency-minimum-result.json")
    assert frozen.frequencies == analysis.frequencies
    assert frozen.payload is not None
    assert frozen.payload.analysis == analysis


# ── happy path ─────────────────────────────────────────────────────────────


def test_converged_synthetic_minimum_with_frequency_product_is_supported() -> None:
    optimize = _result("optimize-converged-result.json")
    frequency = _result("frequency-minimum-result.json")

    validation = validate_stationary_point(optimize, frequency)
    assert validation.status == "supported"
    assert validation.minimum.reason == "affirmed"
    for claim_id in SUPPORTED_CLAIM_IDS:
        claim = validation.claim(claim_id)
        assert claim is not None, claim_id
        assert claim.status == "supported", claim_id
        assert claim.validator_id.startswith("check.")
        assert claim.validator_version == VALIDATOR_VERSION

    assert validation.imaginary_mode_count == 0
    assert validation.mode_count == 3
    assert validation.soft_modes_cm1 == ()
    assert validation.optimized_structure_digest == validation.frequency_structure_digest
    assert validation.optimized_structure_digest is not None

    report = validation.to_dict()
    assert json.loads(json.dumps(report)) == report
    assert report["status"] == "supported"
    assert report["thresholds"]["low_frequency_threshold_cm1"] == (LOW_FREQUENCY_THRESHOLD_CM1)
    assert report["thresholds"]["imaginary_mode_threshold_cm1"] == (IMAGINARY_MODE_THRESHOLD_CM1)
    assert report["thresholds"]["coordinate_abs_tolerance_angstrom"] == (
        COORDINATE_ABS_TOLERANCE_ANGSTROM
    )


# ── contradictions ─────────────────────────────────────────────────────────


def test_unconverged_optimization_is_refuted_never_a_minimum() -> None:
    optimize = _result("optimize-unconverged-result.json")
    frequency = _result("frequency-minimum-result.json")

    convergence = validate_optimization_convergence(optimize)
    assert convergence.status == "refuted"
    assert convergence.reason == "contradiction"

    validation = validate_stationary_point(optimize, frequency)
    assert validation.status == "refuted"
    assert validation.minimum.status != "supported"
    assert validation.minimum.reason == "contradiction"
    assert validation.minimum.details["blocked_by"] == "c-optimization-converged"
    assert "never be reported as a minimum" in validation.minimum.notes[0]
    # Convergence short-circuits: no downstream frequency claim is fabricated.
    assert [claim.claim_id for claim in validation.claims] == [
        "c-optimization-converged",
        "c-stationary-point-minimum",
    ]


def test_frequency_from_a_different_structure_is_refuted() -> None:
    optimize = _result("optimize-converged-result.json")
    wrong = _result("frequency-wrong-structure-result.json")

    membership = validate_frequency_membership(optimize, wrong)
    assert membership.status == "refuted"
    assert membership.reason == "contradiction"
    assert membership.details["max_component_delta_angstrom"] is not None
    assert membership.details["max_component_delta_angstrom"] > COORDINATE_ABS_TOLERANCE_ANGSTROM
    assert (
        membership.details["optimized_structure_digest"]
        != membership.details["frequency_structure_digest"]
    )

    validation = validate_stationary_point(optimize, wrong)
    assert validation.status == "refuted"
    assert validation.minimum.status == "refuted"


def test_wrong_method_and_wrong_electronic_state_are_refuted() -> None:
    optimize = _result("optimize-converged-result.json")

    wrong_method = _result("frequency-wrong-method-result.json")
    method_claim = validate_method_consistency(optimize, wrong_method)
    assert method_claim.status == "refuted"
    assert validate_stationary_point(optimize, wrong_method).status == "refuted"

    wrong_state = _result("frequency-wrong-state-result.json")
    state_claim = validate_electronic_state_consistency(optimize, wrong_state)
    assert state_claim.status == "refuted"
    assert state_claim.details["state_declared_on"] == "both"
    assert validate_stationary_point(optimize, wrong_state).status == "refuted"


# ── missing evidence vs no check ───────────────────────────────────────────


def test_absent_hessian_is_inconclusive_never_supported() -> None:
    optimize = _result("optimize-converged-result.json")
    absent = _result("frequency-absent-hessian-result.json")

    evidence = validate_frequency_evidence(absent)
    assert evidence.status == "inconclusive"
    assert evidence.reason == "missing_evidence"
    assert "frequency_not_computed" in _check_ids(evidence)

    validation = validate_stationary_point(optimize, absent)
    assert validation.status in {"inconclusive", "not_evaluated"}
    assert validation.status != "supported"
    assert validation.minimum.status == "inconclusive"
    assert "frequency_not_computed" in _check_ids(validation.minimum)


def test_absent_results_and_wrong_task_are_not_evaluated() -> None:
    validation = validate_stationary_point(None, None)
    assert validation.status == "not_evaluated"
    assert validation.minimum.reason == "not_checked"
    assert "not_executed" in _check_ids(validation.minimum)

    optimize = _result("optimize-converged-result.json")
    wrong_task = validate_frequency_membership(optimize, optimize)
    assert wrong_task.status == "not_evaluated"
    assert wrong_task.reason == "not_checked"
    assert wrong_task.details["frequency_task"] == "optimize"


def test_completed_optimization_without_convergence_declaration_is_inconclusive() -> None:
    payload = _load_json(SYNTHETIC / "optimize-converged-result.json")
    payload["payload"].pop("optimization_status")
    result = _result_from_payload(payload)

    claim = validate_optimization_convergence(result)
    assert claim.status == "inconclusive"
    assert claim.reason == "missing_evidence"
    assert "convergence_not_met" in _check_ids(claim)
    assert validate_stationary_point(result, None).status == "inconclusive"


def test_the_four_statuses_are_distinct_and_mutually_exclusive() -> None:
    optimize = _result("optimize-converged-result.json")
    statuses = {
        validate_stationary_point(optimize, _result("frequency-minimum-result.json")).status,
        validate_stationary_point(
            _result("optimize-unconverged-result.json"),
            _result("frequency-minimum-result.json"),
        ).status,
        validate_stationary_point(optimize, _result("frequency-absent-hessian-result.json")).status,
        validate_stationary_point(None, None).status,
    }
    assert statuses == {"supported", "refuted", "inconclusive", "not_evaluated"}


# ── thresholds change behaviour, not hidden magic ──────────────────────────


def test_low_real_mode_is_reported_as_soft_but_does_not_refute() -> None:
    optimize = _result("optimize-converged-result.json")
    soft = _mutated_minimum_frequency(**{"6": 35.0})

    validation = validate_stationary_point(optimize, soft)
    assert validation.status == "supported"
    assert validation.soft_modes_cm1 == (35.0,)
    assert any("soft-mode threshold" in note for note in validation.minimum.notes)
    assert LOW_FREQUENCY_THRESHOLD_CM1 == 50.0


def test_imaginary_mode_refutes_the_minimum_claim() -> None:
    optimize = _result("optimize-converged-result.json")
    imaginary = _mutated_minimum_frequency(**{"6": -120.0})

    validation = validate_stationary_point(optimize, imaginary)
    assert validation.status == "refuted"
    imaginary_claim = validation.claim("c-no-imaginary-modes")
    assert imaginary_claim is not None
    assert imaginary_claim.status == "refuted"
    assert imaginary_claim.details["imaginary_mode_count"] == 1
    assert imaginary_claim.details["imaginary_mode_threshold_cm1"] == (IMAGINARY_MODE_THRESHOLD_CM1)


def test_undeclared_electronic_state_is_a_limitation_not_a_passed_check() -> None:
    optimize_payload = _load_json(SYNTHETIC / "optimize-converged-result.json")
    frequency_payload = _load_json(SYNTHETIC / "frequency-minimum-result.json")
    optimize_payload["payload"].pop("electronic_state")
    frequency_payload["payload"].pop("electronic_state")

    validation = validate_stationary_point(
        _result_from_payload(optimize_payload),
        _result_from_payload(frequency_payload),
    )
    assert validation.status == "supported"
    state_claim = validation.claim("c-electronic-state-consistent")
    assert state_claim is not None
    assert state_claim.status == "not_evaluated"
    assert state_claim.details["state_declared_on"] == "neither"
    assert any("electronic-state identity" in note for note in validation.minimum.notes)
