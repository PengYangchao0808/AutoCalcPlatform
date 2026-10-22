"""A-19b: the ``science:validate-da-report`` scenario and EvidenceReport type.

Every report, provenance record, execution fact and artifact reference used
here is **constructed** and labelled ``constructed: true``.  The tests prove
the contract path (claim-by-claim assembly -> validator/source/digest checks ->
provenance attachment -> scenario/claim/H4 separation) without claiming a real
QC run.  The real row stays ``NOT_VERIFIED`` because the B20a-exec artifacts
and the platform ``da-runtime`` / ``da-research-live`` registry do not exist on
this host.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "scripts" / "acceptance" / "acp2_science.py"
FIXTURE_MANIFEST = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "acp2"
    / "science"
    / "diels_alder"
    / "v1"
    / "fixture-manifest.json"
)
REPORT_DIR = FIXTURE_MANIFEST.parent / "report-scenario"
REPORT_MANIFEST = REPORT_DIR / "fixture-manifest.json"
VALID_REPORT = REPORT_DIR / "constructed-evidence-report.json"
COMPLETED_INSUFFICIENT_REPORT = (
    REPORT_DIR / "constructed-evidence-report-completed-insufficient.json"
)
MISSING_SOURCE_REPORT = REPORT_DIR / "constructed-evidence-report-missing-source.json"
DIGEST_MISMATCH_REPORT = REPORT_DIR / "constructed-evidence-report-digest-mismatch.json"
UNKNOWN_STATUS_REPORT = REPORT_DIR / "constructed-evidence-report-unknown-status.json"
MODEL_VALIDATOR_REPORT = REPORT_DIR / "constructed-evidence-report-model-validator.json"
NO_PROVENANCE_REPORT = REPORT_DIR / "constructed-evidence-report-no-provenance.json"


def _load_harness() -> Any:
    import importlib.util

    name = "acp2_science_harness_da_report_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invoke(
    tmp_path: Path,
    *,
    report: Any = VALID_REPORT,
    input_manifest: str | None = None,
    out_name: str = "evidence",
) -> int:
    harness = _load_harness()
    manifest = str(report) if input_manifest is None else input_manifest
    argv = [
        "--scenario",
        "science:validate-da-report",
        "--fixture",
        str(FIXTURE_MANIFEST),
        "--input-manifest",
        manifest,
        "--out",
        str(tmp_path / out_name),
        "--timeout",
        "300",
    ]
    return int(harness.main(argv))


def _record(tmp_path: Path, out_name: str = "evidence") -> dict[str, Any]:
    return json.loads((tmp_path / out_name / "result.json").read_text(encoding="utf-8"))


def _science(tmp_path: Path, out_name: str = "evidence") -> dict[str, Any]:
    return _record(tmp_path, out_name)["extensions"]["science"]


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _wrapper(path: Path = VALID_REPORT) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["constructed"] is True
    return payload


# ── fixtures ───────────────────────────────────────────────────────────────


def test_constructed_report_fixtures_are_labelled_and_digested() -> None:
    manifest = json.loads(REPORT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["constructed"] is True
    assert manifest["real_run"] is False
    assert set(manifest["fixture_files"]) == {
        "valid_report",
        "completed_insufficient_report",
        "missing_source_report",
        "digest_mismatch_report",
        "unknown_status_report",
        "model_validator_report",
        "no_provenance_report",
    }
    for relative, digest in manifest["file_digests"].items():
        assert hashlib.sha256((REPORT_DIR / relative).read_bytes()).hexdigest() == digest

    valid = _wrapper()
    assert valid["schema"] == "acp2.science.constructed-evidence-report"
    assert valid["real_run"] is False
    assert valid["read_root"] == "ts-scenario/artifacts"
    assert valid["report"]["schema_version"] == 1
    assert valid["provenance"]["schema_version"] == 1
    for claim in valid["report"]["claims"]:
        assert claim["status"] in {"supported", "refuted", "inconclusive", "not_evaluated"}
        assert claim["validator"]["validator_id"].startswith(("check.", "software.", "human."))
        assert claim["validator"]["validator_version"]
        assert claim["limitations"]


# ── harness happy path ─────────────────────────────────────────────────────


def test_constructed_valid_report_is_scenario_pass_with_separate_claim_and_h4(
    tmp_path: Path, capsys: Any
) -> None:
    assert _invoke(tmp_path) == 0
    captured = capsys.readouterr().out
    record = _record(tmp_path)
    science = _science(tmp_path)

    assert record["result"] == "PASS"
    assert record["exit_code"] == 0
    assert science["scenario_result"] == "PASS"
    assert science["constructed_fixture"] is True
    assert science["real_run"] is False
    assert science["claim_status"] == "inconclusive"
    statuses = {row["claim_id"]: row for row in science["claim_statuses"]}
    assert statuses["c-ts-first-order-saddle-candidate"]["status"] == "supported"
    assert statuses["c-ts-connects-intended-endpoints"]["status"] == "supported"
    assert statuses["c-electronic-activation-barrier-comparable"]["status"] == "inconclusive"
    assert science["h4"]["closed"] is False
    assert science["h4"]["status"] == "open"
    assert science["h4"]["positive_evidence"] is False
    assert science["model_generated_conclusions_are_evidence"] is False
    assert [step["step_id"] for step in science["completed_steps"]] == [
        "ts_optimize",
        "ts_frequency",
        "irc",
    ]
    assert all(step["terminal_state"] == "completed" for step in science["completed_steps"])

    assert "scenario result: PASS" in captured
    assert "claim status: inconclusive" in captured
    assert "H4 readiness: open (closed=False, positive_evidence=False)" in captured
    assert "claim-status table:" in captured
    assert "c-ts-first-order-saddle-candidate" in captured
    assert "check.ts_saddle_candidate@1.0.0" in captured
    assert "failed candidates: 2" in captured
    assert "conformer_search=none" in captured

    for name in (
        "fixture-validation.json",
        "evidence-report.json",
        "provenance.json",
        "provenance-audit.json",
        "report-limits.json",
        "claim-statuses.json",
        "h4-readiness.json",
        "artifact-digests.json",
    ):
        assert (tmp_path / "evidence" / name).is_file(), name

    # Every claim exposes status, validator id/version, source refs and limitations.
    assert science["claims"]
    for claim in science["claims"]:
        assert claim["status"] in {"supported", "refuted", "inconclusive", "not_evaluated"}
        assert claim["validator"]["validator_id"]
        assert claim["validator"]["validator_version"]
        assert isinstance(claim["source_refs"], list)
        assert claim["limitations"]
    for row in science["claim_statuses"]:
        assert row["validator_id"]
        assert row["validator_version"]
        assert "source_refs" in row
        assert "limitations" in row


def test_a_claim_cannot_be_generated_from_a_non_completed_step(tmp_path: Path) -> None:
    payload = _wrapper()
    payload["completed_steps"][0]["terminal_state"] = "failed"
    non_completed = _write_json(tmp_path / "non-completed.json", payload)
    assert _invoke(tmp_path, report=non_completed, out_name="non-completed") == 1
    record = _record(tmp_path, "non-completed")
    assert record["result"] == "FAIL"
    assert "not 'completed'" in record["reason"]


def test_a_claim_source_must_be_produced_or_consumed_by_a_completed_step(
    tmp_path: Path,
) -> None:
    payload = _wrapper()
    payload["completed_steps"][2]["inputs"] = []
    payload["completed_steps"][2]["artifact"] = "ts-optimize-result.json"
    payload["completed_steps"][2]["sha256"] = payload["completed_steps"][0]["sha256"]
    unbacked = _write_json(tmp_path / "unbacked.json", payload)
    assert _invoke(tmp_path, report=unbacked, out_name="unbacked") == 1
    record = _record(tmp_path, "unbacked")
    assert record["result"] == "FAIL"
    assert "not backed by a completed step" in record["reason"]


def test_completed_but_insufficient_evidence_is_inconclusive_not_supported(
    tmp_path: Path, capsys: Any
) -> None:
    assert _invoke(tmp_path, report=COMPLETED_INSUFFICIENT_REPORT) == 0
    captured = capsys.readouterr().out
    science = _science(tmp_path)
    assert science["scenario_result"] == "PASS"
    assert science["claim_status"] == "inconclusive"
    assert science["claims"][0]["status"] == "inconclusive"
    assert science["claims"][0]["missing_checks"][0]["check_id"] == "partial_output"
    assert science["h4"]["closed"] is False
    assert "claim status: inconclusive" in captured


def test_empty_input_manifest_reports_exact_b20a_exec_and_registry_gaps(
    tmp_path: Path,
) -> None:
    assert _invoke(tmp_path, input_manifest="") == 2
    record = _record(tmp_path)
    science = _science(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert record["exit_code"] == 2
    assert "B20a-exec:da-execution-artifacts" in record["reason"]
    assert "B.B20a.exec" in record["reason"]
    assert "22b" in record["reason"]
    assert "da-runtime" in record["reason"]
    assert "da-research-live" in record["reason"]
    assert science["gaps"]
    assert science["h4"]["closed"] is False
    assert science["h4"]["status"] == "open"
    assert science["claim_statuses"] == []


# ── harness rejection paths ───────────────────────────────────────────────


def test_supported_claim_without_source_is_rejected(tmp_path: Path) -> None:
    assert _invoke(tmp_path, report=MISSING_SOURCE_REPORT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert (
        "cannot be 'supported' without at least one digested source reference" in record["reason"]
    )


def test_source_digest_mismatch_is_fail_not_inconclusive(tmp_path: Path) -> None:
    assert _invoke(tmp_path, report=DIGEST_MISMATCH_REPORT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "digest mismatch" in record["reason"]
    assert "0000000000000000000000000000000000000000000000000000000000000000" in record["reason"]


def test_unknown_claim_status_is_rejected(tmp_path: Path) -> None:
    assert _invoke(tmp_path, report=UNKNOWN_STATUS_REPORT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "UnknownClaimStatusError" in record["reason"]
    assert "confirmed" in record["reason"]


def test_model_text_validator_is_rejected(tmp_path: Path) -> None:
    assert _invoke(tmp_path, report=MODEL_VALIDATOR_REPORT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "model-generated" in record["reason"]
    assert "llm:consensus" in record["reason"]


def test_missing_provenance_is_rejected(tmp_path: Path) -> None:
    assert _invoke(tmp_path, report=NO_PROVENANCE_REPORT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "no provenance object" in record["reason"]


def test_tampered_report_digest_and_provenance_attachment_are_rejected(
    tmp_path: Path,
) -> None:
    payload = _wrapper()
    payload["report"]["provenance_digest"]["value"] = "0" * 64
    tampered = _write_json(tmp_path / "tampered.json", payload)
    assert _invoke(tmp_path, report=tampered, out_name="digest") == 1
    record = _record(tmp_path, "digest")
    assert record["result"] == "FAIL"
    assert "provenance_digest does not match" in record["reason"]


def test_a_model_narrative_cannot_be_marked_as_evidence(tmp_path: Path) -> None:
    payload = _wrapper()
    payload["model_narrative"] = {
        "model_id": "constructed-model",
        "text": "The barrier is low.",
        "is_evidence": True,
    }
    narrative = _write_json(tmp_path / "narrative.json", payload)
    assert _invoke(tmp_path, report=narrative, out_name="narrative") == 1
    record = _record(tmp_path, "narrative")
    assert record["result"] == "FAIL"
    assert "model-generated natural-language conclusion" in record["reason"]


def test_harness_source_does_not_import_acp_or_start_a_job() -> None:
    source = HARNESS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "cccp" not in imported
    assert "acp" not in imported
    assert "acp_services" not in imported
    assert "subprocess" not in imported


# ── science-layer contract unit tests ─────────────────────────────────────


def _digest(value: str = "a" * 64) -> Any:
    from cccp.analysis.research.evidence import Digest

    return Digest(value)


def _scope(subject: str = "subject") -> Any:
    from cccp.analysis.research.evidence import ClaimScope

    return ClaimScope(subject=subject, applicability="system_specific", conditions=None)


def _validator() -> Any:
    from cccp.analysis.research.evidence import EvidenceValidator

    return EvidenceValidator("check.example", "1.0.0", "deterministic")


def _source() -> Any:
    from cccp.analysis.research.evidence import ArtifactSourceRef

    return ArtifactSourceRef(
        artifact_path="artifact.json",
        digest=_digest(),
        role="native_output",
        units="hartree",
    )


def _execution(state: str = "completed") -> Any:
    from cccp.analysis.research.evidence import ExecutionFacts

    return ExecutionFacts(terminal_state=state, argv=("constructed",), exit_code=0)


def _claim(status: str, **overrides: Any) -> Any:
    from cccp.analysis.research.evidence import Claim

    payload: dict[str, Any] = {
        "claim_id": "c-example",
        "statement": "An example claim.",
        "status": status,
        "scope": _scope(),
        "source_refs": (_source(),),
        "validator": _validator(),
        "limitations": (),
    }
    payload.update(overrides)
    return Claim(**payload)


def test_evidence_report_round_trips_the_schema_shape() -> None:
    from cccp.analysis.research.evidence import EvidenceReport, Subject

    report = EvidenceReport(
        report_id="report-example",
        subject=Subject("subject-example", "calculation_result"),
        execution=_execution(),
        claims=(_claim("supported"),),
        limitations=(),
    )
    again = EvidenceReport.from_dict(report.to_dict())
    assert again.to_dict() == report.to_dict()
    assert again.claim_statuses == {"c-example": "supported"}


def test_supported_claim_requires_a_validator() -> None:
    from cccp.analysis.research.evidence import ClaimEvidenceError

    with pytest.raises(ClaimEvidenceError, match="without a validator"):
        _claim("supported", validator=None)


def test_supported_claim_requires_a_digested_source() -> None:
    from cccp.analysis.research.evidence import ClaimEvidenceError

    with pytest.raises(ClaimEvidenceError, match="without at least one digested source"):
        _claim("supported", source_refs=())


def test_refuted_claim_requires_evidence_too() -> None:
    from cccp.analysis.research.evidence import ClaimEvidenceError

    with pytest.raises(ClaimEvidenceError, match="without a validator"):
        _claim("refuted", validator=None, source_refs=())
    with pytest.raises(ClaimEvidenceError, match="without at least one digested source"):
        _claim("refuted", source_refs=())


def test_claim_constructor_rejects_unknown_status() -> None:
    from cccp.analysis.research.evidence import UnknownClaimStatusError

    with pytest.raises(UnknownClaimStatusError, match="confirmed"):
        _claim("confirmed")


def test_not_evaluated_requires_an_explicit_missing_check() -> None:
    from cccp.analysis.research.evidence import ClaimEvidenceError

    with pytest.raises(ClaimEvidenceError, match="lists no missing check"):
        _claim("not_evaluated", source_refs=(), validator=None)


def test_assemble_report_keeps_execution_facts_out_of_claims() -> None:
    from cccp.analysis.research.evidence import (
        ClaimDraft,
        EvidenceValidator,
        ExecutionFacts,
        Limitation,
        Subject,
        assemble_evidence_report,
    )

    execution = ExecutionFacts(
        terminal_state="completed",
        argv=("constructed",),
        exit_code=0,
        started_at="2026-10-23T00:00:00Z",
    )
    complete = ClaimDraft(
        claim_id="c-complete",
        statement="A claim with digested evidence.",
        status="supported",
        scope=_scope("complete"),
        source_refs=(_source(),),
        validator=EvidenceValidator("check.example", "1.0.0", "deterministic"),
        limitations=("lim-example",),
    )
    incomplete = ClaimDraft(
        claim_id="c-incomplete",
        statement="A claim whose artifact is missing.",
        status="supported",
        scope=_scope("incomplete"),
        validator=EvidenceValidator("check.example", "1.0.0", "deterministic"),
        limitations=("lim-example",),
    )
    report = assemble_evidence_report(
        report_id="report-assembled",
        subject=Subject("subject-assembled", "calculation_result"),
        execution=execution,
        drafts=(complete, incomplete),
        limitations=(
            Limitation(
                "lim-example",
                "An example limitation.",
                ("c-complete", "c-incomplete"),
            ),
        ),
    )
    assert report.claim_statuses == {"c-complete": "supported", "c-incomplete": "inconclusive"}
    assert report.execution.terminal_state == "completed"
    for claim in report.to_dict()["claims"]:
        assert "execution" not in claim
        assert "terminal_state" not in claim
        assert "exit_code" not in claim
    assert report.overall_claim_status == "inconclusive"


def test_completed_task_without_sources_is_downgraded_to_inconclusive() -> None:
    from cccp.analysis.research.evidence import ClaimDraft, ExecutionFacts, assemble_claim

    draft = ClaimDraft(
        claim_id="c-example",
        statement="An example claim.",
        status="supported",
        scope=_scope(),
        source_refs=(),
        validator=_validator(),
    )
    claim = assemble_claim(draft, ExecutionFacts("completed", ("constructed",), 0))
    assert claim.status == "inconclusive"
    assert any(check.check_id == "artifact_missing" for check in claim.missing_checks)

    not_run = assemble_claim(draft, ExecutionFacts("failed", ("constructed",), 1))
    assert not_run.status == "not_evaluated"
    assert any(check.check_id == "not_executed" for check in not_run.missing_checks)


def test_validator_assessment_is_assembled_through_the_evidence_floor() -> None:
    from types import SimpleNamespace

    from cccp.analysis.research.evidence import (
        EvidenceMissingCheck,
        FailedCandidate,
        ReportLimits,
        assemble_claim,
        claim_draft_from_assessment,
    )

    assessment = SimpleNamespace(
        claim_id="c-ts-first-order-saddle-candidate",
        statement="A local first-order saddle candidate.",
        status="supported",
        validator_id="check.ts_saddle_candidate",
        validator_version="1.0.0",
        missing_checks=(),
    )
    draft = claim_draft_from_assessment(assessment, scope=_scope("transition_state"))
    claim = assemble_claim(draft, _execution())
    assert claim.status == "inconclusive"
    assert any(check.check_id == "artifact_missing" for check in claim.missing_checks)

    draft_with_source = claim_draft_from_assessment(
        assessment,
        scope=_scope("transition_state"),
        source_refs=(_source(),),
        limitations=("lim-constructed",),
    )
    supported = assemble_claim(draft_with_source, _execution())
    assert supported.status == "supported"
    assert supported.validator is not None
    assert supported.validator.validator_id == "check.ts_saddle_candidate"

    limits = ReportLimits(
        conformation_sampling={"conformer_search": "none"},
        tolerances={"coordinate_abs_tolerance_angstrom": 0.001},
        model_boundaries=("gas phase",),
        failed_candidates=(
            FailedCandidate("cand-002", "candidate_selection", "rejected", "cap consumed"),
        ),
    )
    assert limits.to_dict()["failed_candidates"][0]["reason"] == "cap consumed"
    with pytest.raises(ValueError, match="failed_candidate.status"):
        FailedCandidate("cand-003", "candidate_selection", "maybe", "unknown disposition")

    assert EvidenceMissingCheck("artifact_missing", "no artifact").to_dict()["blocking"] is True


def test_model_narrative_and_model_validator_are_not_evidence() -> None:
    from cccp.analysis.research.evidence import (
        EvidenceValidator,
        ModelConclusionError,
        ModelNarrative,
    )

    with pytest.raises(ModelConclusionError, match="not a validated fact"):
        ModelNarrative(text="The barrier is low.", model_id="constructed-model", is_evidence=True)
    with pytest.raises(ModelConclusionError, match="model/LLM text is never a validator"):
        EvidenceValidator("llm:consensus", "1", "model")


def test_provenance_requires_method_basis_state_software_and_conditions() -> None:
    from cccp.analysis.research.provenance import (
        BasisIdentity,
        Conditions,
        ElectronicStateIdentity,
        ElectronicStructureDerivation,
        InputIdentity,
        MethodIdentity,
        ProvenanceArtifactRef,
        ProvenanceContractError,
        ProvenanceDerivationError,
        PureAnalysisDerivation,
        ScientificProvenance,
        SoftwareIdentity,
    )

    source = ProvenanceArtifactRef("artifact.json", _digest(), "native_output", "hartree")
    base = dict(
        provenance_id="prov-example",
        result_kind="electronic_structure",
        software=SoftwareIdentity("orca", "5.0.4"),
        derivation=ElectronicStructureDerivation(
            MethodIdentity("wB97X-D4", "dft"),
            BasisIdentity("def2-TZVP"),
            ElectronicStateIdentity(0, 1),
        ),
        input=InputIdentity((source,)),
        conditions=Conditions(298.15, "1_bar_ideal_gas", None),
    )
    provenance = ScientificProvenance(**base)
    assert provenance.content_digest().value
    assert provenance.plugin_versions == {}

    with pytest.raises(ProvenanceContractError, match="missing required field"):
        ScientificProvenance.from_dict(
            {
                "schema_version": 1,
                "provenance_id": "prov-example",
                "result_kind": "electronic_structure",
                "software": {"software_id": "orca"},
                "derivation": {},
                "input": {"artifacts": []},
                "conditions": {},
            }
        )
    with pytest.raises(ProvenanceContractError, match="unknown field"):
        ScientificProvenance.from_dict({**provenance.to_dict(), "unexpected": True})
    with pytest.raises(ProvenanceDerivationError, match="must not declare quantum"):
        PureAnalysisDerivation.from_dict(
            {
                "algorithm": "imaginary_mode_count",
                "algorithm_version": "1.0.0",
                "input_digest": _digest().to_dict(),
                "method": {"method_id": "wB97X-D4"},
            }
        )


def test_conditions_must_declare_the_three_nullable_keys() -> None:
    from cccp.analysis.research.provenance import Conditions, ProvenanceContractError

    with pytest.raises(ProvenanceContractError, match="missing required field"):
        Conditions.from_dict({"temperature_kelvin": 298.15})


def test_claim_sources_are_resolved_and_digest_checked(tmp_path: Path) -> None:
    from cccp.analysis.research.evidence import (
        ArtifactSourceRef,
        Claim,
        ClaimScope,
        Digest,
        EvidenceDigestMismatchError,
        EvidenceReport,
        EvidenceSourceUnavailableError,
        EvidenceValidator,
        ExecutionFacts,
        Subject,
        verify_claim_sources,
    )

    artifact = tmp_path / "native.json"
    artifact.write_text('{"energy": -1.0}\n', encoding="utf-8")
    digest = Digest.from_bytes(artifact.read_bytes())
    claim = Claim(
        claim_id="c-example",
        statement="An example claim.",
        status="supported",
        scope=ClaimScope("subject", "system_specific", None),
        source_refs=(ArtifactSourceRef("native.json", digest, "native_output", "hartree"),),
        validator=EvidenceValidator("check.example", "1.0.0", "deterministic"),
    )
    report = EvidenceReport(
        report_id="report-example",
        subject=Subject("subject-example", "calculation_result"),
        execution=ExecutionFacts("completed", ("constructed",), 0),
        claims=(claim,),
    )
    rows = verify_claim_sources(report, tmp_path)
    assert rows[0].actual_digest == digest.value

    missing = Claim(
        claim_id="c-missing",
        statement="An example claim.",
        status="supported",
        scope=ClaimScope("subject", "system_specific", None),
        source_refs=(ArtifactSourceRef("absent.json", digest, "native_output", "hartree"),),
        validator=EvidenceValidator("check.example", "1.0.0", "deterministic"),
    )
    missing_report = EvidenceReport(
        report_id="report-missing",
        subject=Subject("subject-example", "calculation_result"),
        execution=ExecutionFacts("completed", ("constructed",), 0),
        claims=(missing,),
    )
    with pytest.raises(EvidenceSourceUnavailableError, match="absent"):
        verify_claim_sources(missing_report, tmp_path)

    artifact.write_text('{"energy": -2.0}\n', encoding="utf-8")
    with pytest.raises(EvidenceDigestMismatchError, match="digest mismatch"):
        verify_claim_sources(report, tmp_path)
